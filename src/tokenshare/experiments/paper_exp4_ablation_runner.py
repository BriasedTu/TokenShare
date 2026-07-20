"""Experiment 4 protocol-ablation module built on Gate B contracts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from tokenshare.experiments.paper_ablation import (
    ABLATION_SCOPE,
    PaperAblationMode,
    ablation_profile_for_mode,
)
from tokenshare.experiments.paper_experiment_contracts import (
    ExperimentSummaryRows,
    FrozenCaseSelection,
    FrozenCaseSelectionBatch,
    FrozenConditionSelectionBinding,
    PaperExecutionContext,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    UNSUPPORTED_PAPER_TRANSPORTS,
)


EXP4_EXPERIMENT_ID = "exp4_real_ai_protocol_ablation"
EXP4_SUITE_VERSION = "paper_v1"
EXP4_CATALOG_VERSION = "v2"
EXP4_SUPPORTED_CATALOG_VERSIONS = ("v1", "v2")
EXP4_REPEATS = 3
EXP4_WORKER_COUNT = 10
EXP4_TASKS_PER_DIFFICULTY = 5
EXP4_FACTOR_CASE_COUNTS_BY_DIFFICULTY = {
    "easy": 167,
    "medium": 167,
    "hard": 166,
}
EXP4_V1_ROOT_RUN_COUNT = 540
EXP4_ROOT_RUN_COUNT = 9_270
EXP4_SEED_BASE = 4000

BASELINE_PROVIDER_CONFIG_ID = "exp1_baseline_siliconflow"
BASELINE_MODEL_ENTRY_ID = "glm_5_2_exp1_baseline"
BASELINE_PROVIDER_FAMILY = "siliconflow"
BASELINE_PROVIDER_MODEL_ID = "zai-org/GLM-5.2"
BASELINE_REQUEST_CONTROLS = {
    "max_tokens": 1024,
    "timeout_seconds": 30,
    "max_provider_attempts": 1,
    "temperature": 0.0,
    "top_p": 1.0,
    "stream": False,
    "enable_thinking": False,
}
EXP4_CATALOG_VIEW_SCHEMA_VERSION = "tokenshare.paper_exp4_catalog_view.v1"
EXP4_CATALOG_SOURCE_KIND = "paper_input_catalog_manifest"
EXP4_SHARED_LEAN_SLICE_EXPERIMENT_IDS = ("exp2", "exp4", "exp5")
EXP4_EVIDENCE_SCHEMA_VERSION = "tokenshare.paper_exp4_ablation_evidence.v1"
EXP4_CONDITION_EVIDENCE_SCHEMA_VERSION = (
    "tokenshare.paper_exp4_condition_evidence.v1"
)

EXP4_MODES = (
    PaperAblationMode.FULL,
    PaperAblationMode.NO_VERIFICATION,
    PaperAblationMode.NO_PARSER_POLICY,
    PaperAblationMode.NO_REQUEUE,
    PaperAblationMode.NO_MERGE_GATE,
    PaperAblationMode.NO_SLOT_INTEGRITY,
)
FACTOR_PAPER_DIFFICULTIES = ("easy", "medium", "hard")
LEAN_PAPER_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
LEAN_CONDITION_DIFFICULTY = {
    "simple": "easy",
    "medium_lemma_dag": "medium",
    "hard_frontier": "hard",
}
LEAN_TOPIC_ALLOCATIONS = {
    "simple": {"pure_logic": 2, "function_set": 2, "induction": 1},
    "medium_lemma_dag": {"pure_logic": 1, "function_set": 2, "induction": 2},
    "hard_frontier": {"pure_logic": 2, "function_set": 1, "induction": 2},
}
LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER = "mixed_topic_family"


class Exp4MixedLeanCaseSelection(FrozenCaseSelection):
    """Gate B selection carrying one mixed-topic Lean five-task slice."""

    def __post_init__(self) -> None:
        if self.domain != "lean_proof" or self.topic_family is not None:
            super().__post_init__()
            return
        if self.schema_version != "tokenshare.paper_frozen_case_selection.v1":
            raise ValueError(
                "schema_version must be tokenshare.paper_frozen_case_selection.v1"
            )
        for field_name in (
            "selection_id",
            "experiment_id",
            "suite_version",
            "catalog_version",
            "domain",
            "paper_difficulty",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))
        if self.experiment_id != EXP4_EXPERIMENT_ID:
            raise ValueError("selection must belong to Experiment 4")
        if self.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("Lean paper_difficulty is not valid for Experiment 4")
        _require_complete_digest("catalog_digest", self.catalog_digest)
        if not isinstance(self.paper_eligible_required, bool):
            raise ValueError("paper_eligible_required must be a bool")
        normalized_case_ids = _normalize_ordered_case_ids(self.ordered_case_ids)
        object.__setattr__(self, "ordered_case_ids", normalized_case_ids)
        normalized_blocked_reason = _normalize_blocked_reason(self.blocked_reason)
        object.__setattr__(self, "blocked_reason", normalized_blocked_reason)
        _require_non_negative_int(
            "expected_ai_unit_count",
            self.expected_ai_unit_count,
        )
        if normalized_blocked_reason is None:
            if not normalized_case_ids or self.expected_ai_unit_count < 1:
                raise ValueError(
                    "executable selection must declare ordered case ids and AI units"
                )
        elif normalized_case_ids or self.expected_ai_unit_count != 0:
            raise ValueError("blocked selection must not declare cases or AI units")
        object.__setattr__(
            self,
            "topic_family_marker",
            LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER,
        )

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body = super()._body(include_digest=include_digest)
        if self.domain == "lean_proof" and self.topic_family is None:
            body["topic_family_marker"] = LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER
        return body


@dataclass(frozen=True, kw_only=True)
class Exp4ModeExecutionConfig:
    condition_id: str
    ablation_mode: str
    output_root: str
    disabled_mechanisms: tuple[str, ...]
    expected_risk_flags: tuple[str, ...]
    scope: str = ABLATION_SCOPE
    requires_provider_attempt_before_ablation: bool = True
    system_default_behavior_changed: bool = False
    schema_version: str = "tokenshare.paper_exp4_mode_execution_config.v1"

    def __post_init__(self) -> None:
        _require_non_empty_string("condition_id", self.condition_id)
        _require_non_empty_string("output_root", self.output_root)
        mode = PaperAblationMode(self.ablation_mode)
        profile = ablation_profile_for_mode(mode)
        if tuple(self.disabled_mechanisms) != profile.disabled_mechanisms:
            raise ValueError("disabled_mechanisms do not match ablation profile")
        if tuple(self.expected_risk_flags) != profile.expected_risk_flags:
            raise ValueError("expected_risk_flags do not match ablation profile")
        if self.scope != ABLATION_SCOPE:
            raise ValueError("ablation scope must be experiment_boundary")
        if self.requires_provider_attempt_before_ablation is not True:
            raise ValueError("ablation still requires a provider attempt")
        if self.system_default_behavior_changed is not False:
            raise ValueError("Experiment 4 must not change default FULL behavior")
        object.__setattr__(
            self,
            "disabled_mechanisms",
            tuple(self.disabled_mechanisms),
        )
        object.__setattr__(
            self,
            "expected_risk_flags",
            tuple(self.expected_risk_flags),
        )

    @property
    def config_digest(self) -> str:
        return canonical_contract_digest(self._body(include_digest=False))

    def to_dict(self) -> dict[str, Any]:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "ablation_mode": self.ablation_mode,
            "output_root": self.output_root,
            "disabled_mechanisms": list(self.disabled_mechanisms),
            "expected_risk_flags": list(self.expected_risk_flags),
            "scope": self.scope,
            "requires_provider_attempt_before_ablation": (
                self.requires_provider_attempt_before_ablation
            ),
            "system_default_behavior_changed": self.system_default_behavior_changed,
        }
        if include_digest:
            body["config_digest"] = self.config_digest
        return body


class Experiment4AblationModule:
    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        return expand_exp4_conditions(context)

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> FrozenCaseSelectionBatch:
        return freeze_exp4_case_selections(context, conditions)

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        return run_exp4_condition(context, condition, selection)

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        return summarize_exp4_ablation(evidence)


def expand_exp4_conditions(
    context: PaperExecutionContext,
) -> tuple[PaperExperimentCondition, ...]:
    catalog_digest = _catalog_digest(context)
    endpoint_binding = _validate_approved_endpoint_binding(context)
    conditions: list[PaperExperimentCondition] = []
    for domain in ("factorization", "lean_proof"):
        paper_difficulties = (
            FACTOR_PAPER_DIFFICULTIES
            if domain == "factorization"
            else LEAN_PAPER_DIFFICULTIES
        )
        for paper_difficulty in paper_difficulties:
            difficulty = (
                paper_difficulty
                if domain == "factorization"
                else LEAN_CONDITION_DIFFICULTY[paper_difficulty]
            )
            for mode in EXP4_MODES:
                for repeat_id in range(EXP4_REPEATS):
                    conditions.append(
                        _condition(
                            domain=domain,
                            difficulty=difficulty,
                            paper_difficulty=paper_difficulty,
                            mode=mode,
                            repeat_id=repeat_id,
                            catalog_digest=catalog_digest,
                            endpoint_binding=endpoint_binding,
                        )
                    )
    return tuple(conditions)


def freeze_exp4_case_selections(
    context: PaperExecutionContext,
    conditions: Sequence[PaperExperimentCondition],
) -> FrozenCaseSelectionBatch:
    bindings: list[FrozenConditionSelectionBinding] = []
    for condition in conditions:
        canonical_condition = validate_exp4_condition(context, condition)
        selection = (
            _factorization_selection(context, canonical_condition)
            if canonical_condition.domain == "factorization"
            else _lean_selection(context, canonical_condition)
        )
        bindings.append(
            FrozenConditionSelectionBinding.from_condition(
                canonical_condition,
                selection,
            )
        )
    return FrozenCaseSelectionBatch(bindings)


def count_exp4_root_runs(
    conditions: Sequence[PaperExperimentCondition],
    selections: Sequence[FrozenCaseSelection],
) -> int:
    if len(conditions) != len(selections):
        raise ValueError("conditions and selections must have the same length")
    total = 0
    for condition, selection in zip(conditions, selections, strict=True):
        _validate_selection_condition_shape(selection, condition)
        if selection.is_executable:
            expected_count = _selection_case_count(
                catalog_version=selection.catalog_version,
                domain=condition.domain,
                paper_difficulty=str(condition.paper_difficulty),
            )
            if len(selection.ordered_case_ids) != expected_count:
                raise ValueError("Experiment 4 selection count drift")
            total += len(selection.ordered_case_ids)
    return total


def validate_exp4_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> PaperExperimentCondition:
    endpoint_binding = _validate_approved_endpoint_binding(context)
    if condition.experiment_id != EXP4_EXPERIMENT_ID:
        raise ValueError("condition must belong to Experiment 4 ablation")
    if condition.ablation_mode not in {mode.value for mode in EXP4_MODES}:
        raise ValueError("Experiment 4 condition has an unsupported ablation mode")
    if condition.worker_count != EXP4_WORKER_COUNT:
        raise ValueError("Experiment 4 worker_count must remain fixed at 10")
    if condition.fault_type != "none" or float(condition.fault_rate) != 0.0:
        raise ValueError("Experiment 4 conditions must not inject faults")
    if condition.model_policy != "fixed_entry":
        raise ValueError("Experiment 4 requires fixed_entry model policy")
    if (
        condition.provider_config_id != BASELINE_PROVIDER_CONFIG_ID
        or condition.model_entry_id != BASELINE_MODEL_ENTRY_ID
        or condition.provider_family != BASELINE_PROVIDER_FAMILY
        or condition.provider_model_id != BASELINE_PROVIDER_MODEL_ID
        or condition.reasoning_profile_id != endpoint_binding["reasoning_profile_id"]
    ):
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model identity")
    if (
        condition.source_provider_config_digest
        != endpoint_binding["source_provider_config_digest"]
        or condition.model_endpoint_identity_digest
        != endpoint_binding["model_endpoint_identity_digest"]
    ):
        raise ValueError("Experiment 4 condition endpoint binding digest mismatch")
    if condition.repeat_id not in range(EXP4_REPEATS):
        raise ValueError("Experiment 4 repeat_id must be 0, 1, or 2")
    if condition.topic_family is not None:
        raise ValueError("Experiment 4 condition must use one mixed five-task batch")
    if condition.domain == "factorization":
        if condition.paper_difficulty not in FACTOR_PAPER_DIFFICULTIES:
            raise ValueError("factorization difficulty must be easy, medium, or hard")
        expected_difficulty = str(condition.paper_difficulty)
    else:
        if condition.domain != "lean_proof":
            raise ValueError("domain must be factorization or lean_proof")
        if condition.paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
            raise ValueError("Lean paper_difficulty is not valid for Experiment 4")
        expected_difficulty = LEAN_CONDITION_DIFFICULTY[
            str(condition.paper_difficulty)
        ]
    canonical = _condition(
        domain=condition.domain,
        difficulty=expected_difficulty,
        paper_difficulty=str(condition.paper_difficulty),
        mode=PaperAblationMode(condition.ablation_mode),
        repeat_id=condition.repeat_id,
        catalog_digest=_catalog_digest(context),
        endpoint_binding=endpoint_binding,
    )
    if condition.condition_digest != canonical.condition_digest:
        raise ValueError("condition does not match canonical Experiment 4 condition")
    return canonical


def build_exp4_mode_execution_config(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> Exp4ModeExecutionConfig:
    canonical_condition = validate_exp4_condition(context, condition)
    profile = ablation_profile_for_mode(canonical_condition.ablation_mode)
    normalized_base = context.output_root.replace("\\", "/").rstrip("/")
    output_root = "/".join(
        (
            normalized_base,
            EXP4_EXPERIMENT_ID,
            canonical_condition.ablation_mode.lower(),
            canonical_condition.condition_id,
        )
    )
    return Exp4ModeExecutionConfig(
        condition_id=canonical_condition.condition_id,
        ablation_mode=canonical_condition.ablation_mode,
        output_root=output_root,
        disabled_mechanisms=profile.disabled_mechanisms,
        expected_risk_flags=profile.expected_risk_flags,
        scope=profile.scope,
        requires_provider_attempt_before_ablation=(
            profile.requires_provider_attempt_before_ablation
        ),
        system_default_behavior_changed=profile.system_default_behavior_changed,
    )


def run_exp4_condition(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> PaperConditionResult:
    canonical_condition = validate_exp4_condition(context, condition)
    canonical_selection = (
        _factorization_selection(context, canonical_condition)
        if canonical_condition.domain == "factorization"
        else _lean_selection(context, canonical_condition)
    )
    _validate_selection_condition_shape(selection, canonical_condition)
    if selection.selection_digest != canonical_selection.selection_digest:
        raise ValueError("selection digest does not match canonical Experiment 4 slice")
    if canonical_selection.is_blocked:
        return PaperConditionResult(
            condition_id=canonical_condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=EXP4_TASKS_PER_DIFFICULTY,
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=EXP4_TASKS_PER_DIFFICULTY,
            provider_attempt_count=0,
            metrics_ref=None,
        )
    mode_config = build_exp4_mode_execution_config(context, canonical_condition)
    ablation_profile = ablation_profile_for_mode(
        canonical_condition.ablation_mode
    )
    result = context.execution_callback(
        context=context,
        condition=canonical_condition,
        selection=canonical_selection,
        experiment_id=EXP4_EXPERIMENT_ID,
        mode_config=mode_config,
        ablation_profile=ablation_profile,
        output_root=mode_config.output_root,
    )
    if not isinstance(result, PaperConditionResult):
        raise ValueError("execution_callback must return PaperConditionResult")
    if result.condition_id != canonical_condition.condition_id:
        raise ValueError("execution_callback returned a different condition_id")
    return result


def summarize_exp4_ablation(evidence: Any) -> ExperimentSummaryRows:
    execution_scope, catalog_digest, catalog_version, records = (
        _exp4_evidence_envelope(evidence)
    )
    grouped: dict[
        tuple[str, str, str],
        list[tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]],
    ] = defaultdict(list)
    condition_ids: set[str] = set()
    condition_digests: set[str] = set()
    identity_signatures: set[tuple[str, ...]] = set()
    for record in records:
        if record.get("schema_version") != EXP4_CONDITION_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported Experiment 4 condition evidence schema")
        domain = _required_string(record, "domain")
        if domain not in {"factorization", "lean_proof"}:
            raise ValueError("evidence domain must be factorization or lean_proof")
        paper_difficulty = _required_string(record, "paper_difficulty")
        _validate_summary_difficulty(domain, paper_difficulty)
        mode = PaperAblationMode(_required_string(record, "ablation_mode")).value
        condition_id = _required_string(record, "condition_id")
        if condition_id in condition_ids:
            raise ValueError("duplicate condition_id in Experiment 4 evidence")
        condition_ids.add(condition_id)
        condition_digest = record.get("condition_digest")
        _require_complete_digest("condition_digest", condition_digest)
        if condition_digest in condition_digests:
            raise ValueError("duplicate condition_digest in Experiment 4 evidence")
        condition_digests.add(str(condition_digest))
        repeat_id = record.get("repeat_id")
        _require_non_negative_int("repeat_id", repeat_id)
        if repeat_id not in range(EXP4_REPEATS):
            raise ValueError("Experiment 4 evidence repeat_id must be 0, 1, or 2")
        if record.get("seed") != EXP4_SEED_BASE + repeat_id:
            raise ValueError("Experiment 4 evidence seed drifted")
        if record.get("worker_count") != EXP4_WORKER_COUNT:
            raise ValueError("Experiment 4 evidence worker_count drifted")
        if record.get("fault_type") != "none" or record.get("fault_rate") != 0.0:
            raise ValueError("Experiment 4 evidence must not contain fault injection")
        if record.get("catalog_digest") != catalog_digest:
            raise ValueError("Experiment 4 evidence catalog_digest drifted")
        if record.get("catalog_version") != catalog_version:
            raise ValueError("Experiment 4 evidence catalog_version drifted")
        _require_complete_digest("selection_digest", record.get("selection_digest"))
        ordered_case_ids = _normalize_ordered_case_ids(
            record.get("ordered_case_ids")
        )
        if not ordered_case_ids:
            raise ValueError("Experiment 4 evidence requires ordered case IDs")
        identity_signature = _validate_summary_model_identity(record)
        identity_signatures.add(identity_signature)
        transport_kind = _required_string(record, "transport_kind")
        if not isinstance(record.get("paper_eligible"), bool):
            raise ValueError("paper_eligible must be a bool")
        if (
            transport_kind in UNSUPPORTED_PAPER_TRANSPORTS
            and record["paper_eligible"] is True
        ):
            raise ValueError(
                f"{transport_kind} transport cannot be paper eligible"
            )
        if execution_scope == "formal":
            if transport_kind != "ai_api":
                raise ValueError("formal evidence requires ai_api transport")
            if record["paper_eligible"] is not True:
                raise ValueError("formal evidence requires paper-eligible conditions")
        elif record["paper_eligible"] is True:
            raise ValueError("pilot evidence cannot be paper eligible")
        _require_non_negative_int(
            "condition_wall_clock_ms",
            record.get("condition_wall_clock_ms"),
        )
        tasks = _summary_task_results(record.get("task_results"))
        if tuple(task["case_id"] for task in tasks) != ordered_case_ids:
            raise ValueError(
                "task result case IDs must match the frozen ordered case IDs"
            )
        grouped[(domain, paper_difficulty, mode)].append((record, tasks))

    if len(identity_signatures) != 1:
        raise ValueError("Experiment 4 evidence model identity drifted across modes")
    if execution_scope == "formal":
        _validate_complete_formal_evidence(
            grouped,
            record_count=len(records),
            catalog_version=catalog_version,
        )

    rows = [
        _summarize_exp4_group(
            key,
            group_records,
            execution_scope=execution_scope,
            catalog_digest=catalog_digest,
            catalog_version=catalog_version,
        )
        for key, group_records in sorted(grouped.items())
    ]
    return ExperimentSummaryRows(
        experiment_id=EXP4_EXPERIMENT_ID,
        rows=tuple(rows),
    )


def _summarize_exp4_group(
    key: tuple[str, str, str],
    records: Sequence[
        tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]
    ],
    *,
    execution_scope: str,
    catalog_digest: str,
    catalog_version: str,
) -> dict[str, Any]:
    domain, paper_difficulty, mode = key
    tasks = [task for _, record_tasks in records for task in record_tasks]
    repeat_ids = sorted(record["repeat_id"] for record, _ in records)
    if len(repeat_ids) != len(set(repeat_ids)):
        raise ValueError("duplicate repeat_id in Experiment 4 summary group")
    repeat_set_status = (
        "complete"
        if tuple(repeat_ids) == tuple(range(EXP4_REPEATS))
        else "pilot_partial"
    )
    repeat_wall_clocks = [
        record["condition_wall_clock_ms"] for record, _ in records
    ]
    repeat_token_counts = [
        sum(task["total_tokens"] for task in record_tasks)
        for _, record_tasks in records
    ]
    repeat_costs = [
        sum(task["cost_estimate"] for task in record_tasks)
        for _, record_tasks in records
    ]
    root_run_count = len(tasks)
    completed_count = sum(task["root_status"] == "completed" for task in tasks)
    accepted_valid_count = sum(task["accepted_validity"] is True for task in tasks)
    exposed_error_count = sum(task["exposed_error_count"] for task in tasks)
    escaped_error_count = sum(task["escaped_error_count"] for task in tasks)
    applicability, escape_rate = _error_escape_rate(
        mode=PaperAblationMode(mode),
        exposed_error_count=exposed_error_count,
        escaped_error_count=escaped_error_count,
    )
    transport_kinds = {
        _required_string(record, "transport_kind")
        for record, _ in records
    }
    selection_digests = {
        _required_string(record, "selection_digest") for record, _ in records
    }
    ordered_case_id_sets = {
        tuple(record["ordered_case_ids"]) for record, _ in records
    }
    if len(selection_digests) != 1 or len(ordered_case_id_sets) != 1:
        raise ValueError("Experiment 4 selection drifted across repeats")
    return {
        "execution_scope": execution_scope,
        "catalog_digest": catalog_digest,
        "catalog_version": catalog_version,
        "domain": domain,
        "paper_difficulty": paper_difficulty,
        "topic_family": (
            "not_applicable"
            if domain == "factorization"
            else LEAN_BATCH_SELECTION_TOPIC_FAMILY_MARKER
        ),
        "ablation_mode": mode,
        "condition_ids": [record["condition_id"] for record, _ in records],
        "repeat_ids": repeat_ids,
        "repeat_count": len(repeat_ids),
        "expected_repeat_ids": list(range(EXP4_REPEATS)),
        "repeat_set_status": repeat_set_status,
        "selection_digest": next(iter(selection_digests)),
        "ordered_case_ids": list(next(iter(ordered_case_id_sets))),
        "unique_case_count": len({task["case_id"] for task in tasks}),
        "root_run_count": root_run_count,
        "completed_root_count": completed_count,
        "completion_rate": completed_count / root_run_count,
        "accepted_valid_count": accepted_valid_count,
        "accepted_validity_rate": (
            accepted_valid_count / root_run_count
            if root_run_count > 0
            else None
        ),
        "wrong_canonical_acceptance_count": _flag_count(
            tasks,
            "wrong_canonical_acceptance",
        ),
        "wrong_canonical_acceptance_rate": _flag_rate(
            tasks,
            "wrong_canonical_acceptance",
        ),
        "raw_only_acceptance_count": _flag_count(
            tasks,
            "raw_only_acceptance",
        ),
        "raw_only_acceptance_rate": _flag_rate(
            tasks,
            "raw_only_acceptance",
        ),
        "stuck_task_count": _flag_count(tasks, "stuck_task"),
        "stuck_task_rate": _flag_rate(tasks, "stuck_task"),
        "premature_merge_count": _flag_count(tasks, "premature_merge"),
        "premature_merge_rate": _flag_rate(tasks, "premature_merge"),
        "slot_mismatch_count": _flag_count(tasks, "slot_mismatch"),
        "slot_mismatch_rate": _flag_rate(tasks, "slot_mismatch"),
        "wall_clock_ms": _median(repeat_wall_clocks),
        "wall_clock_median_ms": _median(repeat_wall_clocks),
        "wall_clock_iqr_ms": _iqr(repeat_wall_clocks),
        "wall_clock_total_ms": sum(repeat_wall_clocks),
        "total_tokens": sum(repeat_token_counts),
        "total_tokens_median": _median(repeat_token_counts),
        "total_tokens_iqr": _iqr(repeat_token_counts),
        "cost": sum(repeat_costs),
        "cost_estimate": sum(repeat_costs),
        "cost_median": _median(repeat_costs),
        "cost_iqr": _iqr(repeat_costs),
        "exposed_error_count": exposed_error_count,
        "escaped_error_count": escaped_error_count,
        "error_escape_rate": escape_rate,
        "error_escape_applicability": applicability,
        "transport_kind": (
            next(iter(transport_kinds))
            if len(transport_kinds) == 1
            else "mixed"
        ),
        "paper_eligible": all(
            record["paper_eligible"] is True
            for record, _ in records
        )
        and execution_scope == "formal"
        and repeat_set_status == "complete",
    }


def _error_escape_rate(
    *,
    mode: PaperAblationMode,
    exposed_error_count: int,
    escaped_error_count: int,
) -> tuple[str, float | None]:
    if escaped_error_count > exposed_error_count:
        raise ValueError("escaped_error_count cannot exceed exposed_error_count")
    if mode in {PaperAblationMode.FULL, PaperAblationMode.NO_REQUEUE}:
        return "not_applicable", None
    if exposed_error_count == 0:
        return "zero_denominator", None
    return "applicable", escaped_error_count / exposed_error_count


def _validate_summary_model_identity(record: Mapping[str, Any]) -> tuple[str, ...]:
    expected = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
    }
    for field_name, expected_value in expected.items():
        if record.get(field_name) != expected_value:
            raise ValueError("Experiment 4 summary model identity drifted")
    reasoning_profile_id = _required_string(record, "reasoning_profile_id")
    source_digest = record.get("source_provider_config_digest")
    endpoint_digest = record.get("model_endpoint_identity_digest")
    _require_complete_digest("source_provider_config_digest", source_digest)
    _require_complete_digest("model_endpoint_identity_digest", endpoint_digest)
    controls = _validated_request_controls(
        record.get("request_controls"),
        field_name="summary request controls",
        error_message="Experiment 4 summary request controls drifted",
    )
    return (
        BASELINE_PROVIDER_CONFIG_ID,
        BASELINE_MODEL_ENTRY_ID,
        BASELINE_PROVIDER_FAMILY,
        BASELINE_PROVIDER_MODEL_ID,
        reasoning_profile_id,
        str(source_digest),
        str(endpoint_digest),
        canonical_contract_digest(controls),
    )


def _validate_complete_formal_evidence(
    grouped: Mapping[
        tuple[str, str, str],
        Sequence[tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]],
    ],
    *,
    record_count: int,
    catalog_version: str,
) -> None:
    if record_count != 108:
        raise ValueError("formal evidence must contain 108 conditions")
    expected_groups = {
        (domain, paper_difficulty, mode.value)
        for domain, difficulties in (
            ("factorization", FACTOR_PAPER_DIFFICULTIES),
            ("lean_proof", LEAN_PAPER_DIFFICULTIES),
        )
        for paper_difficulty in difficulties
        for mode in EXP4_MODES
    }
    if set(grouped) != expected_groups:
        raise ValueError("formal evidence must contain the complete Exp4 matrix")
    root_run_count = 0
    slice_signatures: dict[
        tuple[str, str],
        set[tuple[str, tuple[str, ...]]],
    ] = defaultdict(set)
    for (domain, paper_difficulty, _), records in grouped.items():
        repeat_ids = sorted(record["repeat_id"] for record, _ in records)
        if repeat_ids != list(range(EXP4_REPEATS)):
            raise ValueError("formal repeat set must be exactly 0, 1, 2")
        for record, tasks in records:
            expected_count = _selection_case_count(
                catalog_version=catalog_version,
                domain=domain,
                paper_difficulty=paper_difficulty,
            )
            if len(tasks) != expected_count:
                raise ValueError("formal Experiment 4 condition count drift")
            root_run_count += len(tasks)
            slice_signatures[(domain, paper_difficulty)].add(
                (
                    str(record["selection_digest"]),
                    tuple(record["ordered_case_ids"]),
                )
            )
    expected_root_runs = _expected_root_runs(catalog_version)
    if root_run_count != expected_root_runs:
        raise ValueError("formal Experiment 4 root-run count drift")
    if any(len(signatures) != 1 for signatures in slice_signatures.values()):
        raise ValueError("formal Experiment 4 case selection drifted across modes")


def _summary_task_results(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("task_results must be a non-empty list or tuple")
    normalized: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    case_ids: set[str] = set()
    allowed_root_statuses = {
        "completed",
        "failed",
        "blocked",
        "timeout",
        "budget_exhausted",
        "ineligible",
    }
    for item in value:
        task = dict(_mapping(item, "task_result"))
        task_id = _required_string(task, "task_id")
        if task_id in task_ids:
            raise ValueError("duplicate task_id in one condition result")
        task_ids.add(task_id)
        case_id = _required_string(task, "case_id")
        if case_id in case_ids:
            raise ValueError("duplicate case_id in one condition result")
        case_ids.add(case_id)
        root_status = _required_string(task, "root_status")
        if root_status not in allowed_root_statuses:
            raise ValueError("task_result root_status is invalid")
        accepted_validity = task.get("accepted_validity")
        if accepted_validity is not None and not isinstance(
            accepted_validity,
            bool,
        ):
            raise ValueError("accepted_validity must be a bool or null")
        for field_name in (
            "wrong_canonical_acceptance",
            "raw_only_acceptance",
            "stuck_task",
            "premature_merge",
            "slot_mismatch",
        ):
            if not isinstance(task.get(field_name), bool):
                raise ValueError(f"{field_name} must be a bool")
        _require_non_negative_int(
            "exposed_error_count",
            task.get("exposed_error_count"),
        )
        _require_non_negative_int(
            "escaped_error_count",
            task.get("escaped_error_count"),
        )
        if task["escaped_error_count"] > task["exposed_error_count"]:
            raise ValueError("escaped error must be exposed")
        _require_non_negative_int("wall_clock_ms", task.get("wall_clock_ms"))
        _require_non_negative_int("total_tokens", task.get("total_tokens"))
        _require_non_negative_number("cost_estimate", task.get("cost_estimate"))
        normalized.append(task)
    return tuple(normalized)


def _exp4_evidence_envelope(
    value: Any,
) -> tuple[str, str, str, tuple[Mapping[str, Any], ...]]:
    envelope = _mapping(value, "evidence")
    if envelope.get("schema_version") != EXP4_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported Experiment 4 evidence schema")
    execution_scope = envelope.get("execution_scope")
    if execution_scope not in {"formal", "pilot"}:
        raise ValueError("execution_scope must be formal or pilot")
    if envelope.get("suite_version") != EXP4_SUITE_VERSION:
        raise ValueError("Experiment 4 evidence suite_version drifted")
    catalog_digest = envelope.get("catalog_digest")
    _require_complete_digest("catalog_digest", catalog_digest)
    catalog_version = envelope.get("catalog_version")
    if catalog_version not in EXP4_SUPPORTED_CATALOG_VERSIONS:
        raise ValueError("Experiment 4 evidence catalog_version drifted")
    records = envelope.get("condition_results")
    if not isinstance(records, (list, tuple)) or not records:
        raise ValueError("evidence must contain condition results")
    return (
        str(execution_scope),
        str(catalog_digest),
        str(catalog_version),
        tuple(_mapping(record, "condition_result") for record in records),
    )


def _validate_summary_difficulty(domain: str, paper_difficulty: str) -> None:
    allowed = (
        FACTOR_PAPER_DIFFICULTIES
        if domain == "factorization"
        else LEAN_PAPER_DIFFICULTIES
    )
    if paper_difficulty not in allowed:
        raise ValueError("paper_difficulty does not match evidence domain")


def _selection_case_count(
    *,
    catalog_version: str,
    domain: str,
    paper_difficulty: str,
) -> int:
    if domain == "lean_proof" or catalog_version == "v1":
        return EXP4_TASKS_PER_DIFFICULTY
    if catalog_version == "v2" and domain == "factorization":
        try:
            return EXP4_FACTOR_CASE_COUNTS_BY_DIFFICULTY[paper_difficulty]
        except KeyError as exc:
            raise ValueError("unsupported Factorization paper difficulty") from exc
    raise ValueError("Experiment 4 supports only catalog v1 or v2")


def _expected_root_runs(catalog_version: str) -> int:
    if catalog_version == "v1":
        return EXP4_V1_ROOT_RUN_COUNT
    if catalog_version == "v2":
        return EXP4_ROOT_RUN_COUNT
    raise ValueError("Experiment 4 supports only catalog v1 or v2")


def _flag_count(tasks: Sequence[Mapping[str, Any]], field_name: str) -> int:
    return sum(task[field_name] is True for task in tasks)


def _flag_rate(tasks: Sequence[Mapping[str, Any]], field_name: str) -> float:
    return _flag_count(tasks, field_name) / len(tasks)


def _median(values: Sequence[float | int]) -> float | int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _iqr(values: Sequence[float | int]) -> float | int | None:
    ordered = sorted(values)
    if len(ordered) < 2:
        return 0 if ordered else None
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        lower = ordered[:midpoint]
        upper = ordered[midpoint + 1 :]
    else:
        lower = ordered[:midpoint]
        upper = ordered[midpoint:]
    q1 = _median(lower)
    q3 = _median(upper)
    if q1 is None or q3 is None:
        return 0
    return q3 - q1


def _factorization_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    catalog_view = _exp4_catalog_view(context)
    by_difficulty = _mapping(
        catalog_view.get("factorization_slices"),
        "catalog.factorization_slices",
    )
    cases = _case_list(
        by_difficulty.get(condition.paper_difficulty),
        field_name=f"catalog.factorization_slices.{condition.paper_difficulty}",
    )
    expected_count = _selection_case_count(
        catalog_version=str(catalog_view.get("catalog_version")),
        domain="factorization",
        paper_difficulty=str(condition.paper_difficulty),
    )
    if len(cases) != expected_count:
        raise ValueError("Experiment 4 factorization selection count drift")
    return _selection_from_cases(context, condition, cases)


def _lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
) -> FrozenCaseSelection:
    paper_difficulty = str(condition.paper_difficulty)
    catalog_view = _exp4_catalog_view(context)
    readiness_status = catalog_view["lean_semantic_readiness_status"]
    if readiness_status != "ready":
        reason = str(catalog_view["lean_semantic_readiness_reason"])
        return _blocked_lean_selection(
            context,
            condition,
            reason=f"lean_semantic_readiness_not_passed:{reason}",
        )
    shared_lean_value = catalog_view.get("shared_lean_slices")
    if shared_lean_value is None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=f"missing_shared_lean_slice:{paper_difficulty}",
        )
    shared_lean = _mapping(shared_lean_value, "catalog.shared_lean_slices")
    missing_reason = _missing_lean_slice_reason(
        shared_lean,
        paper_difficulty,
        source="shared",
    )
    if missing_reason is not None:
        return _blocked_lean_selection(
            context,
            condition,
            reason=missing_reason,
        )
    cases = _lean_slice_cases(shared_lean, paper_difficulty, source="shared")
    _validate_shared_lean_slice_digest(
        catalog_view,
        paper_difficulty,
        shared_lean[paper_difficulty],
    )
    return _selection_from_cases(context, condition, cases)


def _missing_lean_slice_reason(
    lean_catalog: Mapping[str, Any],
    paper_difficulty: str,
    *,
    source: str,
) -> str | None:
    if paper_difficulty not in lean_catalog:
        return f"missing_{source}_lean_slice:{paper_difficulty}"
    by_topic_value = lean_catalog[paper_difficulty]
    if not isinstance(by_topic_value, Mapping):
        raise ValueError(f"{source}.{paper_difficulty} must be a mapping")
    for topic_family in LEAN_TOPIC_ALLOCATIONS[paper_difficulty]:
        if topic_family not in by_topic_value:
            return (
                f"missing_{source}_lean_slice:{paper_difficulty}:"
                f"{topic_family}"
            )
    return None


def _blocked_lean_selection(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    *,
    reason: str,
) -> Exp4MixedLeanCaseSelection:
    catalog = _mapping(context.catalog, "catalog")
    return Exp4MixedLeanCaseSelection(
        selection_id=(
            f"{EXP4_EXPERIMENT_ID}:lean_proof:{condition.paper_difficulty}"
        ),
        experiment_id=EXP4_EXPERIMENT_ID,
        suite_version=_non_empty_catalog_value(catalog, "suite_version"),
        catalog_version=_non_empty_catalog_value(catalog, "catalog_version"),
        domain="lean_proof",
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        ordered_case_ids=(),
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=0,
        paper_eligible_required=True,
        blocked_reason=reason,
    )


def _lean_slice_cases(
    lean_catalog: Mapping[str, Any],
    paper_difficulty: str,
    *,
    source: str,
) -> tuple[Mapping[str, Any], ...]:
    by_topic = _mapping(
        lean_catalog.get(paper_difficulty),
        f"{source}.{paper_difficulty}",
    )
    cases: list[Mapping[str, Any]] = []
    for topic_family, expected_count in LEAN_TOPIC_ALLOCATIONS[
        paper_difficulty
    ].items():
        topic_cases = _case_list(
            by_topic.get(topic_family),
            field_name=f"{source}.{paper_difficulty}.{topic_family}",
        )
        if len(topic_cases) != expected_count:
            raise ValueError(
                f"{source} Lean {paper_difficulty}/{topic_family} slice must "
                f"contain {expected_count} roots"
            )
        cases.extend(topic_cases)
    return tuple(cases)


def _selection_from_cases(
    context: PaperExecutionContext,
    condition: PaperExperimentCondition,
    cases: Sequence[Mapping[str, Any]],
) -> FrozenCaseSelection:
    case_ids = tuple(_case_id(case) for case in cases)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Experiment 4 selection contains duplicate case IDs")
    expected_ai_unit_count = sum(
        _positive_int(case.get("expected_ai_unit_count"))
        for case in cases
    )
    catalog = _mapping(context.catalog, "catalog")
    selection_class = (
        Exp4MixedLeanCaseSelection
        if condition.domain == "lean_proof"
        else FrozenCaseSelection
    )
    return selection_class(
        selection_id=(
            f"{EXP4_EXPERIMENT_ID}:{condition.domain}:"
            f"{condition.paper_difficulty}"
        ),
        experiment_id=EXP4_EXPERIMENT_ID,
        suite_version=_non_empty_catalog_value(catalog, "suite_version"),
        catalog_version=_non_empty_catalog_value(catalog, "catalog_version"),
        domain=condition.domain,
        paper_difficulty=str(condition.paper_difficulty),
        topic_family=None,
        ordered_case_ids=case_ids,
        catalog_digest=_catalog_digest(context),
        expected_ai_unit_count=expected_ai_unit_count,
        paper_eligible_required=True,
    )


def _validate_selection_condition_shape(
    selection: FrozenCaseSelection,
    condition: PaperExperimentCondition,
) -> None:
    if selection.experiment_id != EXP4_EXPERIMENT_ID:
        raise ValueError("selection must belong to Experiment 4")
    if selection.domain != condition.domain:
        raise ValueError("selection domain does not match condition")
    if selection.paper_difficulty != condition.paper_difficulty:
        raise ValueError("selection paper_difficulty does not match condition")
    if selection.catalog_digest != condition.catalog_digest:
        raise ValueError("selection catalog digest does not match condition")


def _exp4_catalog_view(context: PaperExecutionContext) -> Mapping[str, Any]:
    view = _mapping(context.catalog, "catalog")
    if view.get("schema_version") != EXP4_CATALOG_VIEW_SCHEMA_VERSION:
        raise ValueError("Experiment 4 requires the prepared catalog view v1")
    if view.get("catalog_source_kind") != EXP4_CATALOG_SOURCE_KIND:
        raise ValueError(
            "Experiment 4 catalog view must derive from PaperInputCatalogManifest"
        )
    if view.get("suite_version") != EXP4_SUITE_VERSION:
        raise ValueError("Experiment 4 catalog suite_version drifted")
    if view.get("catalog_version") not in EXP4_SUPPORTED_CATALOG_VERSIONS:
        raise ValueError("Experiment 4 catalog_version drifted")
    _require_complete_digest("catalog_digest", view.get("catalog_digest"))
    experiment_ids = view.get("shared_lean_slice_experiment_ids")
    if not isinstance(experiment_ids, (list, tuple)) or tuple(experiment_ids) != (
        EXP4_SHARED_LEAN_SLICE_EXPERIMENT_IDS
    ):
        raise ValueError("Experiment 4 requires the shared Exp2/4/5 Lean slices")
    readiness_status = view.get("lean_semantic_readiness_status")
    if readiness_status not in {"ready", "blocked"}:
        raise ValueError("lean_semantic_readiness_status must be ready or blocked")
    readiness_reason = view.get("lean_semantic_readiness_reason")
    if readiness_status == "ready":
        if readiness_reason is not None:
            raise ValueError("ready Lean catalog view must not declare a blocked reason")
    else:
        _require_non_empty_string("lean_semantic_readiness_reason", readiness_reason)
    return view


def _validate_shared_lean_slice_digest(
    catalog_view: Mapping[str, Any],
    paper_difficulty: str,
    by_topic_value: Any,
) -> None:
    by_topic = _mapping(
        by_topic_value,
        f"catalog.shared_lean_slices.{paper_difficulty}",
    )
    digests = _mapping(
        catalog_view.get("shared_lean_slice_digests"),
        "catalog.shared_lean_slice_digests",
    )
    expected_digest = digests.get(paper_difficulty)
    _require_complete_digest("shared_lean_slice_digest", expected_digest)
    ordered_cases = [
        {
            "case_id": _case_id(case),
            "expected_ai_unit_count": _positive_int(
                case.get("expected_ai_unit_count")
            ),
        }
        for topic_family in ("pure_logic", "function_set", "induction")
        for case in _case_list(
            by_topic.get(topic_family),
            field_name=(
                f"catalog.shared_lean_slices.{paper_difficulty}.{topic_family}"
            ),
        )
    ]
    actual_digest = canonical_contract_digest(
        {
            "paper_difficulty": paper_difficulty,
            "topic_allocations": {
                topic_family: len(
                    _case_list(
                        by_topic.get(topic_family),
                        field_name=(
                            "catalog.shared_lean_slices."
                            f"{paper_difficulty}.{topic_family}"
                        ),
                    )
                )
                for topic_family in ("pure_logic", "function_set", "induction")
            },
            "ordered_cases": ordered_cases,
        }
    )
    if actual_digest != expected_digest:
        raise ValueError("Experiment 4 shared Lean slice digest mismatch")


def _case_list(value: Any, *, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple")
    cases: list[Mapping[str, Any]] = []
    for case in value:
        cases.append(_mapping(case, f"{field_name} case"))
    return tuple(cases)


def _case_id(case: Mapping[str, Any]) -> str:
    value = case.get("case_id")
    _require_non_empty_string("case_id", value)
    return str(value)


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("expected_ai_unit_count must be an integer >= 1")
    return value


def _non_empty_catalog_value(catalog: Mapping[str, Any], field_name: str) -> str:
    value = catalog.get(field_name)
    _require_non_empty_string(field_name, value)
    return str(value)


def _condition(
    *,
    domain: str,
    difficulty: str,
    paper_difficulty: str,
    mode: PaperAblationMode,
    repeat_id: int,
    catalog_digest: str,
    endpoint_binding: Mapping[str, Any],
) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id=EXP4_EXPERIMENT_ID,
        condition_id=(
            f"exp4_{domain}_{paper_difficulty}_{mode.value.lower()}_r{repeat_id}"
        ),
        domain=domain,
        difficulty=difficulty,
        paper_difficulty=paper_difficulty,
        topic_family=None,
        worker_count=EXP4_WORKER_COUNT,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode=mode.value,
        model_policy="fixed_entry",
        provider_config_id=BASELINE_PROVIDER_CONFIG_ID,
        model_entry_id=BASELINE_MODEL_ENTRY_ID,
        provider_family=BASELINE_PROVIDER_FAMILY,
        provider_model_id=BASELINE_PROVIDER_MODEL_ID,
        reasoning_profile_id=str(endpoint_binding["reasoning_profile_id"]),
        model_cohort_id=endpoint_binding.get("model_cohort_id"),
        model_cohort_digest=endpoint_binding.get("model_cohort_digest"),
        cohort_member_id=endpoint_binding.get("cohort_member_id"),
        source_provider_config_digest=str(
            endpoint_binding["source_provider_config_digest"]
        ),
        model_endpoint_identity_digest=str(
            endpoint_binding["model_endpoint_identity_digest"]
        ),
        repeat_id=repeat_id,
        seed=EXP4_SEED_BASE + repeat_id,
        catalog_digest=catalog_digest,
    )


def _catalog_digest(context: PaperExecutionContext) -> str:
    catalog = _exp4_catalog_view(context)
    value = catalog.get("catalog_digest")
    _require_complete_digest("catalog_digest", value)
    return str(value)


def _validate_approved_endpoint_binding(
    context: PaperExecutionContext,
) -> Mapping[str, Any]:
    binding = _mapping(
        context.approved_endpoint_binding,
        "approved_endpoint_binding",
    )
    expected = {
        "provider_config_id": BASELINE_PROVIDER_CONFIG_ID,
        "provider_family": BASELINE_PROVIDER_FAMILY,
        "provider_model_id": BASELINE_PROVIDER_MODEL_ID,
    }
    for field_name, expected_value in expected.items():
        if binding.get(field_name) != expected_value:
            raise ValueError(
                f"Experiment 4 requires GLM-5.2 baseline {field_name}"
            )
    reasoning_profile_id = binding.get("reasoning_profile_id")
    _require_non_empty_string("reasoning_profile_id", reasoning_profile_id)
    selected_entry_id = binding.get("selected_entry_id")
    if selected_entry_id != BASELINE_MODEL_ENTRY_ID:
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model entry")
    if binding.get("model_entry_id", selected_entry_id) != BASELINE_MODEL_ENTRY_ID:
        raise ValueError("Experiment 4 requires GLM-5.2 baseline model entry")
    for field_name in (
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ):
        _require_complete_digest(field_name, binding.get(field_name))
    _validated_request_controls(
        binding.get("request_controls"),
        field_name="baseline request controls",
        error_message="Experiment 4 baseline request controls drifted",
    )
    _validated_request_controls(
        context.request_limits,
        field_name="context request controls",
        error_message="Experiment 4 context request controls drifted",
    )
    return binding


def _validated_request_controls(
    value: Any,
    *,
    field_name: str,
    error_message: str,
) -> dict[str, Any]:
    controls = _mapping(value, field_name)
    if set(controls) != set(BASELINE_REQUEST_CONTROLS):
        raise ValueError(error_message)
    for integer_field in (
        "max_tokens",
        "timeout_seconds",
        "max_provider_attempts",
    ):
        item = controls[integer_field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(error_message)
    temperature = controls["temperature"]
    top_p = controls["top_p"]
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or float(temperature) != 0.0
        or isinstance(top_p, bool)
        or not isinstance(top_p, (int, float))
        or float(top_p) != 1.0
        or controls["stream"] is not False
        or controls["enable_thinking"] is not False
        or dict(controls) != BASELINE_REQUEST_CONTROLS
    ):
        raise ValueError(error_message)
    return dict(BASELINE_REQUEST_CONTROLS)


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_complete_digest(field_name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


def _require_non_empty_string(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(field_name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")


def _require_non_negative_number(field_name: str, value: Any) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a number >= 0")


def _required_string(value: Mapping[str, Any], field_name: str) -> str:
    item = value.get(field_name)
    _require_non_empty_string(field_name, item)
    return str(item)


def _normalize_ordered_case_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered_case_ids must be a list or tuple")
    normalized: list[str] = []
    for case_id in value:
        _require_non_empty_string("case_id", case_id)
        normalized.append(case_id)
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate case_id in ordered_case_ids")
    return tuple(normalized)


def _normalize_blocked_reason(value: Any) -> str | None:
    if value is None:
        return None
    _require_non_empty_string("blocked_reason", value)
    return str(value)


__all__ = [
    "BASELINE_MODEL_ENTRY_ID",
    "BASELINE_PROVIDER_FAMILY",
    "BASELINE_PROVIDER_MODEL_ID",
    "EXP4_EXPERIMENT_ID",
    "EXP4_REPEATS",
    "EXP4_ROOT_RUN_COUNT",
    "EXP4_V1_ROOT_RUN_COUNT",
    "Exp4ModeExecutionConfig",
    "Exp4MixedLeanCaseSelection",
    "Experiment4AblationModule",
    "build_exp4_mode_execution_config",
    "count_exp4_root_runs",
    "expand_exp4_conditions",
    "freeze_exp4_case_selections",
    "run_exp4_condition",
    "summarize_exp4_ablation",
    "validate_exp4_condition",
]
