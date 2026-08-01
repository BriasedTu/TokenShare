"""Plan-only budget calculation for paper real-AI experiments."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from typing import Any, Sequence

from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    AIAPIProviderEntry,
    load_ai_api_config,
)

from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
)
from tokenshare.experiments.paper_model_identity import (
    PaperModelEndpointIdentity,
    build_fixed_entry_executor_requirements,
    build_model_endpoint_identity,
    prepare_fixed_entry_execution_config,
    validate_fixed_entry_config_identity,
)
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    PaperBudgetResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_pipeline_profile import (
    OfflineImplementationApproval,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_case_ai_unit_bindings,
)
from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges


class PaperBudgetApprovalError(ValueError):
    pass


def build_response_bank_budget_report(
    *,
    budget: PaperBudgetResult,
    inventory_plan: Any,
    model_ids: Sequence[str],
) -> JsonObject:
    """把既有 plan-only budget 与去重后的 semantic inventory 汇成审计摘要。"""

    semantic_slot_count = int(inventory_plan.expected_slot_count)
    provider_attempt_ceiling = int(budget.max_provider_attempts)
    if semantic_slot_count and provider_attempt_ceiling < 1:
        raise ValueError("response-bank estimate requires provider attempt ceiling")
    token_per_attempt = (
        (int(budget.token_upper_bound) + provider_attempt_ceiling - 1)
        // provider_attempt_ceiling
        if provider_attempt_ceiling
        else 0
    )
    cost_per_attempt = (
        float(budget.cost_upper_bound) / provider_attempt_ceiling
        if provider_attempt_ceiling
        else 0.0
    )
    return {
        "schema_version": "tokenshare.response_bank_budget_report.v1",
        "planned_root_runs": int(budget.planned_root_runs),
        "planned_first_attempt_ai_units": int(budget.planned_ai_units),
        "semantic_slot_count": semantic_slot_count,
        "model_ids": list(model_ids),
        "max_concurrent_roots": int(inventory_plan.max_concurrent_roots),
        "provider_calls_upper": semantic_slot_count,
        "token_upper_bound": token_per_attempt * semantic_slot_count,
        "cost_upper_bound_cny": cost_per_attempt * semantic_slot_count,
        "disk_estimate": dict(budget.disk_estimate),
        "terminal_provider_failure_count": int(
            inventory_plan.terminal_provider_failure_count
        ),
        "terminal_success_count": int(inventory_plan.terminal_success_count),
        "terminal_unacquired_count": int(inventory_plan.terminal_unacquired_count),
    }


EXP1_PILOT_PROFILE_SCHEMA_VERSION = "tokenshare.paper_exp1_pilot_profile.v1"
EXP1_PILOT_EXPERIMENT_ID = "exp1_real_ai_feasibility"
EXP1_BASELINE_COHORT_ID = "tokenshare.paper.exp1_baseline.v1"
EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
EXP5_V3_PROMPT_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT = 4_096
_FORMAL_DISK_POLICY: JsonObject = {
    "schema_version": "tokenshare.paper_disk_calibration.v2",
    "p95_root_bytes": 262_144,
    "p95_ai_unit_bytes": 65_536,
    "p95_attempt_envelope_bytes": 98_304,
    "p95_model_execution_record_bytes": 65_536,
    "model_execution_record_duplicate_multiplier": 2,
    "p95_provider_attempt_payload_bytes": 327_680,
    "provider_attempt_payload_observed_p95_bytes": 289_246,
    "provider_attempt_payload_observed_max_bytes": 1_232_946,
    "provider_attempt_calibration_attempt_count": 88,
    "provider_attempt_payload_sample_count": 87,
    "provider_attempt_payload_calibration_source": "external_read_only_run04",
    "utf8_bytes_per_token": 4,
    "fixed_manifest_bytes": 67_108_864,
    "fixed_temp_bytes": 536_870_912,
}


@dataclass(frozen=True, kw_only=True)
class Exp1PilotProfile:
    """已解析且已绑定 safe provider config 的 Exp1 pilot profile。"""

    body: JsonObject
    source_provider_config: AIAPIExecutorConfig
    model_endpoint_identity: PaperModelEndpointIdentity

    @property
    def profile_digest(self) -> str:
        return digest_json(self.body)

    def to_dict(self) -> JsonObject:
        return {
            **_json_copy(self.body),
            "profile_digest": self.profile_digest,
        }


PAPER_EXPERIMENT_TASK_LIMITS = {
    "exp2_real_ai_scalability": 5,
    "exp4_real_ai_protocol_ablation": 5,
}


def load_exp1_pilot_profile(path: str | Path) -> Exp1PilotProfile:
    """读取冻结的 Exp1 pilot profile，并绑定不含 secret value 的 provider config。"""

    profile_path = Path(path)
    body = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("Exp1 pilot profile must be a JSON object")
    _validate_exp1_pilot_profile_body(body)

    baseline = dict(body["baseline_provider"])
    provider_config_path = profile_path.parent / str(
        baseline["provider_config_path"]
    )
    provider_config_body = json.loads(
        provider_config_path.read_text(encoding="utf-8")
    )
    if not isinstance(provider_config_body, dict):
        raise ValueError("Exp1 baseline provider config must be a JSON object")
    source_config = load_ai_api_config(provider_config_body)
    _validate_provider_config_against_profile(
        profile_body=body,
        source_config=source_config,
    )

    baseline_declaration = {
        "schema_version": "tokenshare.paper_exp1_baseline_declaration.v1",
        "baseline_id": baseline["baseline_id"],
        "provider_config_id": baseline["provider_config_id"],
        "selected_entry_id": baseline["selected_entry_id"],
        "provider_family": baseline["provider_family"],
        "provider_model_id": baseline["provider_model_id"],
        "reasoning_profile_id": baseline["reasoning_profile_id"],
    }
    baseline_declaration_digest = digest_json(baseline_declaration)
    endpoint_identity = build_model_endpoint_identity(
        model_cohort_id=EXP1_BASELINE_COHORT_ID,
        model_cohort_digest=baseline_declaration_digest,
        cohort_member_id=str(baseline["baseline_id"]),
        provider_config_id=str(baseline["provider_config_id"]),
        selected_entry_id=str(baseline["selected_entry_id"]),
        expected_provider_family=str(baseline["provider_family"]),
        expected_provider_model_id=str(baseline["provider_model_id"]),
        expected_reasoning_profile_id=str(baseline["reasoning_profile_id"]),
        source_config=source_config,
    )

    normalized = _json_copy(body)
    normalized_baseline = dict(normalized["baseline_provider"])
    normalized_baseline.update(
        {
            "baseline_declaration_digest": baseline_declaration_digest,
            "source_provider_config_digest": source_config.config_digest,
            "model_endpoint_identity_digest": (
                endpoint_identity.model_endpoint_identity_digest
            ),
            "provider_config_safe_snapshot": source_config.to_safe_dict(),
        }
    )
    normalized["baseline_provider"] = normalized_baseline
    return Exp1PilotProfile(
        body=normalized,
        source_provider_config=source_config,
        model_endpoint_identity=endpoint_identity,
    )


def plan_exp1_pilot(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    pilot_profile: Exp1PilotProfile,
    plan_only: bool,
    approve_budget_digest: str | OfflineImplementationApproval | None = None,
    budget_approval_required: bool = True,
    budget_mode: str | None = None,
) -> PaperBudgetResult:
    """按冻结 profile 复算最小 Exp1 pilot，不进行任何 provider 调用。"""

    if budget_mode not in {None, "unlimited"}:
        raise ValueError("unsupported budget_mode")
    if budget_mode == "unlimited":
        if budget_approval_required or approve_budget_digest is not None:
            raise ValueError("unlimited budget cannot use digest approval")
    profile_body = pilot_profile.to_dict()
    request_policy = dict(profile_body["request_policy"])
    disk_policy = dict(profile_body["disk_policy"])
    selected_entry = _selected_entry(pilot_profile)
    validated_binding = validate_fixed_entry_config_identity(
        expected_identity=pilot_profile.model_endpoint_identity,
        provider_config_id=pilot_profile.model_endpoint_identity.provider_config_id,
        source_config=pilot_profile.source_provider_config,
    )
    executor_requirements_by_domain: dict[str, JsonObject] = {}
    for domain, adapter_metadata_key in (
        ("factorization", "factorization_paper_adapter"),
        ("lean_proof", "lean_paper_adapter"),
    ):
        limits = dict(request_policy["domain_limits"][domain])
        prepared_config = prepare_fixed_entry_execution_config(
            source_config=pilot_profile.source_provider_config,
            binding=validated_binding,
            max_tokens=int(limits["max_tokens"]),
            timeout_seconds=int(limits["timeout_seconds"]),
            adapter_metadata_key=adapter_metadata_key,
        )
        executor_requirements_by_domain[domain] = (
            build_fixed_entry_executor_requirements(
                config=prepared_config,
                binding=validated_binding,
            )
        )
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }

    case_request_profiles: list[JsonObject] = []
    for ordinal, selection in enumerate(profile_body["catalog_slice"]):
        case_id = str(selection["case_id"])
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"Exp1 pilot catalog case not found: {case_id}")
        _validate_selected_case(selection=selection, case=case)
        case_request_profiles.append(
            _case_request_profile(
                ordinal=ordinal,
                selection=selection,
                case=case,
                request_policy=request_policy,
                selected_entry=selected_entry,
                disk_policy=disk_policy,
                executor_requirements=executor_requirements_by_domain[
                    _case_domain(case)
                ],
            )
        )

    repeat_count = int(profile_body["repeat_count"])
    planned_root_runs = len(case_request_profiles) * repeat_count
    executable_root_runs = sum(
        1
        for item in case_request_profiles
        if item["execution_status"] == "executable"
    ) * repeat_count
    blocked_root_runs = planned_root_runs - executable_root_runs
    planned_ai_units = sum(
        int(item["ai_unit_count"]) for item in case_request_profiles
    ) * repeat_count
    max_provider_attempts = sum(
        int(item["provider_attempt_upper_bound"])
        for item in case_request_profiles
    ) * repeat_count
    token_upper_bound = sum(
        int(item["token_upper_bound"]) for item in case_request_profiles
    ) * repeat_count
    cost_upper_bound = round(
        sum(float(item["cost_upper_bound"]) for item in case_request_profiles)
        * repeat_count,
        12,
    )
    wall_clock_estimate = float(
        sum(
            int(item["wall_clock_upper_bound_seconds"])
            for item in case_request_profiles
        )
        * repeat_count
    )
    disk_bytes = (
        int(disk_policy["base_suite_bytes"])
        + planned_root_runs * int(disk_policy["per_root_bytes"])
        + planned_ai_units * int(disk_policy["per_ai_unit_bytes"])
    )

    digest_body: JsonObject = {
        "schema_version": "tokenshare.paper_exp1_pilot_budget_identity.v1",
        "pilot_profile": profile_body,
        "catalog_digest": catalog_manifest.catalog_digest,
        "case_request_profiles": case_request_profiles,
        "planned_experiments": [EXP1_PILOT_EXPERIMENT_ID],
        "planned_conditions": len(case_request_profiles),
        "planned_root_runs": planned_root_runs,
        "executable_root_runs": executable_root_runs,
        "blocked_root_runs": blocked_root_runs,
        "planned_ai_units": planned_ai_units,
        "max_provider_attempts": max_provider_attempts,
        "token_upper_bound": token_upper_bound,
        "cost_upper_bound": cost_upper_bound,
        "wall_clock_estimate": wall_clock_estimate,
        "disk_estimate_bytes": disk_bytes,
    }
    if budget_mode == "unlimited":
        digest_body["budget_authorization"] = {
            "budget_mode": "unlimited",
            "approval_required": False,
            "approval_mode": "explicit_unlimited",
            "authorization_source": "cli",
            "hard_limits": {},
        }
    budget_digest = digest_json(digest_body)
    _validate_budget_approval(
        plan_only=plan_only,
        approve_budget_digest=approve_budget_digest,
        budget_digest=budget_digest,
        budget_approval_required=budget_approval_required,
    )
    budget_approval = _budget_approval_record(
        budget_digest=budget_digest,
        approve_budget_digest=approve_budget_digest,
        budget_approval_required=budget_approval_required,
        budget_mode=budget_mode,
    )

    pilot_summary: JsonObject = {
        "schema_version": "tokenshare.paper_exp1_pilot_budget_summary.v1",
        "suite_id": profile_body["suite_id"],
        "profile_digest": pilot_profile.profile_digest,
        "catalog_digest": catalog_manifest.catalog_digest,
        "domains": list(profile_body["domains"]),
        "worker_count": profile_body["worker_count"],
        "repeat_count": repeat_count,
        "seed_family": list(profile_body["seed_family"]),
        "case_order": [
            str(item["case_id"]) for item in profile_body["catalog_slice"]
        ],
        "executable_root_runs": executable_root_runs,
        "blocked_root_runs": blocked_root_runs,
        "blocked_case_ids": [
            str(item["case_id"])
            for item in case_request_profiles
            if item["execution_status"] == "structured_blocked"
        ],
        "baseline_provider": dict(profile_body["baseline_provider"]),
        "request_policy": request_policy,
        "case_request_profiles": case_request_profiles,
        "estimate_methodology": {
            "token": "prompt budget plus completion max_tokens for every allowed provider attempt",
            "cost": "token ceilings multiplied by the frozen safe provider pricing snapshot",
            "wall_clock": "sequential sum of provider timeout ceilings; local checker time is reported separately by later execution evidence",
            "disk": "base suite allowance plus fixed per-root and per-executable-AI-unit allowances",
        },
        "approval_status": budget_approval["approval_mode"],
    }
    return PaperBudgetResult(
        budget_digest=budget_digest,
        planned_experiments=[EXP1_PILOT_EXPERIMENT_ID],
        planned_conditions=len(case_request_profiles),
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        max_provider_attempts=max_provider_attempts,
        token_upper_bound=token_upper_bound,
        cost_upper_bound=cost_upper_bound,
        wall_clock_estimate=wall_clock_estimate,
        quota_preflight={
            "status": "planned",
            "provider_calls_made": 0,
            "plan_only": plan_only,
            "budget_approval": budget_approval,
            "exp1_pilot": pilot_summary,
        },
        rate_limit_preflight={
            "status": "not_checked",
            "max_in_flight_global": profile_body["worker_count"],
            "provider_calls_made": 0,
        },
        disk_estimate={
            "bytes": disk_bytes,
            "policy": disk_policy,
        },
        status=PaperStatus.PLANNED,
        budget_mode=budget_mode,
        approval_required=(False if budget_mode == "unlimited" else None),
        approval_mode=("explicit_unlimited" if budget_mode == "unlimited" else None),
        authorization_source=("cli" if budget_mode == "unlimited" else None),
        hard_limits=({} if budget_mode == "unlimited" else None),
    )


def build_exp5_v3_token_ceiling_mapping(
    model_endpoint_cohort_preflight: JsonObject | None,
) -> dict[str, int] | None:
    """从已通过的 Exp5 v3 whole-cohort preflight 派生逐端点 token 上界。"""

    if not isinstance(model_endpoint_cohort_preflight, Mapping):
        return None
    if (
        model_endpoint_cohort_preflight.get("cohort_id")
        != PAPER_MODEL_ENDPOINT_COHORT_V3_ID
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        return None
    specs = _exp5_v3_member_budget_specs(
        model_endpoint_cohort_preflight
    )
    return {
        str(specs[member_id]["model_endpoint_identity_digest"]): int(
            specs[member_id]["token_upper_bound_per_provider_attempt"]
        )
        for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    }


def plan_paper_suite(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    conditions: tuple[PaperExperimentCondition, ...] | list[PaperExperimentCondition],
    max_provider_attempts_per_ai_unit: int,
    token_upper_bound_per_provider_attempt: int,
    token_upper_bound_by_endpoint_identity_digest: Mapping[str, int] | None = None,
    cost_upper_bound_per_provider_attempt: float,
    plan_only: bool,
    lean_3x3_matrix: JsonObject | None = None,
    model_policy_preflight: JsonObject | None = None,
    model_endpoint_cohort_preflight: JsonObject | None = None,
    frozen_selections: tuple[JsonObject, ...] | list[JsonObject] | None = None,
    ai_unit_commitments: tuple[JsonObject, ...] | list[JsonObject] | None = None,
    endpoint_identity: JsonObject | None = None,
    request_limits: JsonObject | None = None,
    hard_limits: JsonObject | None = None,
    suite_identity: JsonObject | None = None,
    output_identity: JsonObject | None = None,
    approve_budget_digest: str | OfflineImplementationApproval | None = None,
    budget_approval_required: bool = True,
    budget_mode: str | None = None,
) -> PaperBudgetResult:
    if max_provider_attempts_per_ai_unit < 1:
        raise ValueError("max_provider_attempts_per_ai_unit must be >= 1")
    if token_upper_bound_per_provider_attempt < 1:
        raise ValueError("token_upper_bound_per_provider_attempt must be >= 1")
    if cost_upper_bound_per_provider_attempt < 0:
        raise ValueError("cost_upper_bound_per_provider_attempt must be >= 0")
    if budget_mode not in {None, "unlimited"}:
        raise ValueError("unsupported budget_mode")
    if budget_mode == "unlimited":
        if budget_approval_required:
            raise ValueError("unlimited budget cannot require budget approval")
        if approve_budget_digest is not None:
            raise ValueError("unlimited budget cannot provide an approval digest")
        if hard_limits != {}:
            raise ValueError("unlimited budget requires empty hard_limits")
    condition_tuple = tuple(conditions)
    endpoint_budget_specs = _prepare_exp5_v3_endpoint_budget_specs(
        conditions=condition_tuple,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        token_upper_bound_by_endpoint_identity_digest=(
            token_upper_bound_by_endpoint_identity_digest
        ),
    )
    endpoint_usage_by_member = (
        {
            member_id: {
                "planned_ai_unit_count": 0,
                "provider_attempt_upper_bound": 0,
            }
            for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
        }
        if endpoint_budget_specs is not None
        else None
    )
    exact_selection_by_condition = _exact_selection_commitments(
        frozen_selections,
        conditions=condition_tuple,
    )
    all_cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }
    planned_root_runs = 0
    planned_ai_units = 0
    max_condition_root_runs = 0
    max_condition_ai_units = 0
    max_condition_provider_attempts = 0
    exp4_requeue_ai_unit_upper_bound = 0
    headline_root_runs_by_experiment: dict[str, int] = {}
    headline_ai_units_by_experiment: dict[str, int] = {}
    supporting_root_runs_by_experiment: dict[str, int] = {}
    supporting_ai_units_by_experiment: dict[str, int] = {}
    replacement_reserve_by_experiment: dict[str, int] = {}
    replacement_policy_by_condition: list[JsonObject] = []
    supporting_baseline_commitments: list[JsonObject] = []
    seen_supporting_baselines: set[str] = set()
    derived_selections: list[JsonObject] = []
    derived_ai_unit_commitments: list[JsonObject] = []
    for condition in condition_tuple:
        if condition.catalog_digest != catalog_manifest.catalog_digest:
            raise ValueError("condition catalog_digest does not match catalog manifest")
        exact_selection = exact_selection_by_condition.get(condition.condition_id)
        if exact_selection is not None:
            ordered_case_ids = exact_selection.get("ordered_case_ids", ())
            if exact_selection.get("blocked_reason") is not None:
                cases = ()
            else:
                if not isinstance(ordered_case_ids, (list, tuple)):
                    raise ValueError("frozen selection ordered_case_ids must be a list")
                try:
                    cases = tuple(
                        all_cases_by_id[str(case_id)] for case_id in ordered_case_ids
                    )
                except KeyError as exc:
                    raise ValueError(
                        "frozen selection references an unknown catalog case"
                    ) from exc
        else:
            cases = catalog_manifest.cases_for(
                domain=condition.domain,
                difficulty=condition.difficulty,
                paper_difficulty=condition.paper_difficulty,
                topic_family=condition.topic_family,
            )
            cases = _budget_cases_for_condition(cases, condition=condition)
        if not cases:
            if exact_selection is not None and exact_selection.get("blocked_reason"):
                derived_selections.append(
                    {
                        "condition_id": condition.condition_id,
                        "condition_digest": condition.condition_digest,
                        "ordered_case_ids": [],
                        "blocked_reason": exact_selection["blocked_reason"],
                    }
                )
                continue
            raise ValueError(
                "insufficient catalog cases for condition "
                f"{condition.condition_id}: domain={condition.domain} "
                f"difficulty={condition.difficulty} "
                f"paper_difficulty={condition.paper_difficulty} "
                f"topic_family={condition.topic_family}"
            )
        task_limit = PAPER_EXPERIMENT_TASK_LIMITS.get(condition.experiment_id)
        if exact_selection is None and task_limit is not None:
            cases = cases[:task_limit]
        derived_selections.append(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "ordered_case_ids": [str(case["case_id"]) for case in cases],
            }
        )
        condition_ai_units = 0
        for case in cases:
            split_profile = _budget_split_profile(
                case=case,
                condition=condition,
                exact_selection=exact_selection,
            )
            planned_ai_unit_ids = [
                str(ai_unit_id) for ai_unit_id in split_profile["ai_unit_order"]
            ]
            condition_ai_units += len(planned_ai_unit_ids)
            derived_ai_unit_commitments.append(
                {
                    "condition_id": condition.condition_id,
                    "condition_digest": condition.condition_digest,
                    "case_id": str(case["case_id"]),
                    "planned_ai_unit_ids": planned_ai_unit_ids,
                    "case_digest": digest_json(case),
                    "split_profile_digest": digest_json(split_profile),
                    "commitment_digest": digest_json(
                        {
                            "case": case,
                            "split_profile": split_profile,
                            "seed": condition.seed,
                        }
                    ),
                }
            )
        planned_root_runs += len(cases)
        planned_ai_units += condition_ai_units
        headline_root_runs_by_experiment[condition.experiment_id] = (
            headline_root_runs_by_experiment.get(condition.experiment_id, 0)
            + len(cases)
        )
        headline_ai_units_by_experiment[condition.experiment_id] = (
            headline_ai_units_by_experiment.get(condition.experiment_id, 0)
            + condition_ai_units
        )
        replacement_count = _condition_replacement_reserve(
            condition=condition,
            condition_ai_units=condition_ai_units,
        )
        max_condition_root_runs = max(max_condition_root_runs, len(cases))
        max_condition_ai_units = max(
            max_condition_ai_units,
            condition_ai_units,
        )
        max_condition_provider_attempts = max(
            max_condition_provider_attempts,
            (condition_ai_units + replacement_count)
            * max_provider_attempts_per_ai_unit,
        )
        if replacement_count:
            replacement_reserve_by_experiment[condition.experiment_id] = (
                replacement_reserve_by_experiment.get(
                    condition.experiment_id,
                    0,
                )
                + replacement_count
            )
        replacement_policy_by_condition.append(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "experiment_id": condition.experiment_id,
                "planned_ai_unit_count": condition_ai_units,
                "protocol_replacement_reserve": replacement_count,
                "policy": _condition_replacement_policy(condition),
            }
        )
        if (
            endpoint_usage_by_member is not None
            and condition.experiment_id == EXP5_EXPERIMENT_ID
        ):
            member_id = str(condition.cohort_member_id)
            member_usage = endpoint_usage_by_member[member_id]
            member_usage["planned_ai_unit_count"] += condition_ai_units
            member_usage["provider_attempt_upper_bound"] += (
                (condition_ai_units + replacement_count)
                * max_provider_attempts_per_ai_unit
            )
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            exp4_requeue_ai_unit_upper_bound += replacement_count
        baseline_key = _exp3_supporting_baseline_key(condition)
        if baseline_key is not None and baseline_key not in seen_supporting_baselines:
            seen_supporting_baselines.add(baseline_key)
            supporting_root_runs_by_experiment[condition.experiment_id] = (
                supporting_root_runs_by_experiment.get(
                    condition.experiment_id,
                    0,
                )
                + len(cases)
            )
            supporting_ai_units_by_experiment[condition.experiment_id] = (
                supporting_ai_units_by_experiment.get(
                    condition.experiment_id,
                    0,
                )
                + condition_ai_units
            )
            supporting_baseline_commitments.append(
                {
                    "baseline_key": baseline_key,
                    "experiment_id": condition.experiment_id,
                    "source_condition_id": condition.condition_id,
                    "source_condition_digest": condition.condition_digest,
                    "ordered_case_ids": [
                        str(case["case_id"]) for case in cases
                    ],
                    "root_run_count": len(cases),
                    "planned_ai_unit_count": condition_ai_units,
                    "protocol_replacement_reserve": 0,
                }
            )
    planned_root_runs += sum(supporting_root_runs_by_experiment.values())
    planned_ai_units += sum(supporting_ai_units_by_experiment.values())
    total_replacement_reserve = sum(
        replacement_reserve_by_experiment.values()
    )
    max_provider_attempts = (
        (planned_ai_units + total_replacement_reserve)
        * max_provider_attempts_per_ai_unit
    )
    (
        token_upper_bound,
        cost_upper_bound,
        endpoint_budget_identity,
    ) = _endpoint_aware_budget_totals(
        max_provider_attempts=max_provider_attempts,
        token_upper_bound_per_provider_attempt=(
            token_upper_bound_per_provider_attempt
        ),
        cost_upper_bound_per_provider_attempt=(
            cost_upper_bound_per_provider_attempt
        ),
        endpoint_budget_specs=endpoint_budget_specs,
        endpoint_usage_by_member=endpoint_usage_by_member,
    )
    experiment_ids = sorted(
        set(headline_root_runs_by_experiment)
        | set(supporting_root_runs_by_experiment)
    )
    actual_scheduled_root_runs_by_experiment = {
        experiment_id: (
            headline_root_runs_by_experiment.get(experiment_id, 0)
            + supporting_root_runs_by_experiment.get(experiment_id, 0)
        )
        for experiment_id in experiment_ids
    }
    planned_first_attempt_ai_units_by_experiment = {
        experiment_id: (
            headline_ai_units_by_experiment.get(experiment_id, 0)
            + supporting_ai_units_by_experiment.get(experiment_id, 0)
        )
        for experiment_id in experiment_ids
    }
    experiment_budget_identity: JsonObject = {
        "schema_version": "tokenshare.paper_experiment_budget_identity.v1",
        "headline_root_runs_by_experiment": dict(
            sorted(headline_root_runs_by_experiment.items())
        ),
        "supporting_baseline_root_runs_by_experiment": dict(
            sorted(supporting_root_runs_by_experiment.items())
        ),
        "actual_scheduled_root_runs_by_experiment": dict(
            sorted(actual_scheduled_root_runs_by_experiment.items())
        ),
        "headline_ai_units_by_experiment": dict(
            sorted(headline_ai_units_by_experiment.items())
        ),
        "supporting_baseline_ai_units_by_experiment": dict(
            sorted(supporting_ai_units_by_experiment.items())
        ),
        "planned_first_attempt_ai_units_by_experiment": dict(
            sorted(planned_first_attempt_ai_units_by_experiment.items())
        ),
        "replacement_reserve_by_experiment": dict(
            sorted(replacement_reserve_by_experiment.items())
        ),
        "replacement_policy_by_condition": replacement_policy_by_condition,
        "supporting_baseline_commitments": supporting_baseline_commitments,
        "headline_p0_core_root_runs": sum(
            headline_root_runs_by_experiment.get(experiment_id, 0)
            for experiment_id in (
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            )
        ),
        "headline_p0_full_root_runs": sum(
            headline_root_runs_by_experiment.get(experiment_id, 0)
            for experiment_id in (
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
                "exp5_real_ai_model_endpoint_comparison",
            )
        ),
        "actual_p0_core_root_runs": sum(
            actual_scheduled_root_runs_by_experiment.get(experiment_id, 0)
            for experiment_id in (
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
            )
        ),
        "actual_p0_full_root_runs": sum(
            actual_scheduled_root_runs_by_experiment.get(experiment_id, 0)
            for experiment_id in (
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
                "exp5_real_ai_model_endpoint_comparison",
            )
        ),
        "exp2_no_early_stop_ai_unit_upper_bound": (
            planned_first_attempt_ai_units_by_experiment.get(
                "exp2_real_ai_scalability",
                0,
            )
        ),
        "exp3_replacement_reserve": replacement_reserve_by_experiment.get(
            "exp3_real_ai_fault_recovery",
            0,
        ),
        "exp4_replacement_reserve": replacement_reserve_by_experiment.get(
            "exp4_real_ai_protocol_ablation",
            0,
        ),
        "exp5_first_attempt_ai_units": (
            planned_first_attempt_ai_units_by_experiment.get(
                "exp5_real_ai_model_endpoint_comparison",
                0,
            )
        ),
        "provider_attempt_multiplier": max_provider_attempts_per_ai_unit,
        "max_provider_attempts": max_provider_attempts,
        "provider_calls_made": 0,
    }
    resolved_endpoint_identity = endpoint_identity or {
        "condition_endpoint_identities": _condition_endpoint_identities(
            condition_tuple
        )
    }
    budget_commitments: JsonObject = {
        "frozen_selections": _json_copy(
            list(frozen_selections)
            if frozen_selections is not None
            else derived_selections
        ),
        "ai_unit_commitments": _json_copy(
            list(ai_unit_commitments)
            if ai_unit_commitments is not None
            else derived_ai_unit_commitments
        ),
        "endpoint_identity": _json_copy(resolved_endpoint_identity),
        "request_limits": _json_copy(
            request_limits
            or {
                "max_provider_attempts_per_ai_unit": (
                    max_provider_attempts_per_ai_unit
                ),
                "token_upper_bound_per_provider_attempt": (
                    token_upper_bound_per_provider_attempt
                ),
                "cost_upper_bound_per_provider_attempt": (
                    cost_upper_bound_per_provider_attempt
                ),
            }
        ),
        "hard_limits": _json_copy(
            hard_limits
            if hard_limits is not None
            else {
                "max_provider_attempts": max_provider_attempts,
                "token_upper_bound": token_upper_bound,
                "cost_upper_bound": cost_upper_bound,
            }
        ),
        "suite_identity": _json_copy(
            suite_identity
            or {
                "suite_version": "paper_v1",
                "execution_scope": "formal_matrix",
                "experiment_ids": sorted(
                    {condition.experiment_id for condition in condition_tuple}
                ),
            }
        ),
        "output_identity": _json_copy(
            output_identity
            or {
                "root_policy": "caller_supplied_isolated_output_root",
                "cross_experiment_evidence_allowed": False,
            }
        ),
        "experiment_budget_identity": experiment_budget_identity,
    }
    if endpoint_budget_identity is not None:
        budget_commitments["endpoint_budget_identity"] = (
            endpoint_budget_identity
        )
    body: JsonObject = {
        "planned_experiments": sorted({item.experiment_id for item in condition_tuple}),
        "planned_conditions": len(condition_tuple),
        "planned_root_runs": planned_root_runs,
        "planned_ai_units": planned_ai_units,
        "max_provider_attempts": max_provider_attempts,
        "token_upper_bound": token_upper_bound,
        "cost_upper_bound": cost_upper_bound,
        "catalog_digest": catalog_manifest.catalog_digest,
        "condition_digests": [item.condition_digest for item in condition_tuple],
        "budget_commitments": budget_commitments,
    }
    if exp4_requeue_ai_unit_upper_bound:
        budget_commitments["exp4_requeue_ai_unit_upper_bound"] = (
            exp4_requeue_ai_unit_upper_bound
        )
        body["exp4_requeue_ai_unit_upper_bound"] = (
            exp4_requeue_ai_unit_upper_bound
        )
    if lean_3x3_matrix is not None:
        body["lean_3x3_matrix"] = _lean_matrix_budget_identity(lean_3x3_matrix)
    if model_policy_preflight is not None:
        body["model_policy_preflight"] = model_policy_preflight
    if model_endpoint_cohort_preflight is not None:
        body["model_endpoint_cohort_preflight"] = model_endpoint_cohort_preflight
    disk_estimate = _paper_disk_estimate(
        planned_conditions=len(condition_tuple),
        planned_root_runs=planned_root_runs,
        planned_ai_units=planned_ai_units,
        provider_attempt_upper_bound=max_provider_attempts,
        max_tokens=token_upper_bound_per_provider_attempt,
        token_upper_bound=token_upper_bound,
        max_condition_root_runs=max_condition_root_runs,
        max_condition_ai_units=max_condition_ai_units,
        max_condition_provider_attempts=max_condition_provider_attempts,
    )
    body["disk_estimate"] = disk_estimate
    if budget_mode == "unlimited":
        body["budget_authorization"] = {
            "budget_mode": "unlimited",
            "approval_required": False,
            "approval_mode": "explicit_unlimited",
            "authorization_source": "cli",
            "hard_limits": {},
        }
    budget_digest = digest_json(body)
    _validate_budget_approval(
        plan_only=plan_only,
        approve_budget_digest=approve_budget_digest,
        budget_digest=budget_digest,
        budget_approval_required=budget_approval_required,
    )
    budget_approval = _budget_approval_record(
        budget_digest=budget_digest,
        approve_budget_digest=approve_budget_digest,
        budget_approval_required=budget_approval_required,
        budget_mode=budget_mode,
    )
    return PaperBudgetResult(
        budget_digest=budget_digest,
        planned_experiments=body["planned_experiments"],
        planned_conditions=body["planned_conditions"],
        planned_root_runs=body["planned_root_runs"],
        planned_ai_units=body["planned_ai_units"],
        max_provider_attempts=body["max_provider_attempts"],
        token_upper_bound=body["token_upper_bound"],
        cost_upper_bound=body["cost_upper_bound"],
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={
            "status": "not_checked",
            "provider_calls_made": 0,
            "plan_only": plan_only,
            "budget_approval": budget_approval,
            "budget_commitments": budget_commitments,
            **(
                {
                    "token_upper_bound_by_member": (
                        endpoint_budget_identity["token_upper_bound_by_member"]
                    ),
                    "cost_upper_bound_by_member": (
                        endpoint_budget_identity["cost_upper_bound_by_member"]
                    ),
                    "pricing_snapshot_digest_by_member": (
                        endpoint_budget_identity[
                            "pricing_snapshot_digest_by_member"
                        ]
                    ),
                }
                if endpoint_budget_identity is not None
                else {}
            ),
            **(
                {"lean_3x3_matrix": lean_3x3_matrix}
                if lean_3x3_matrix is not None
                else {}
            ),
            **(
                {"model_policy_preflight": model_policy_preflight}
                if model_policy_preflight is not None
                else {}
            ),
            **(
                {"model_endpoint_cohort_preflight": model_endpoint_cohort_preflight}
                if model_endpoint_cohort_preflight is not None
                else {}
            ),
        },
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate=disk_estimate,
        status=PaperStatus.PLANNED,
        budget_mode=budget_mode,
        approval_required=(False if budget_mode == "unlimited" else None),
        approval_mode=("explicit_unlimited" if budget_mode == "unlimited" else None),
        authorization_source=("cli" if budget_mode == "unlimited" else None),
        hard_limits=({} if budget_mode == "unlimited" else None),
    )


def _paper_disk_estimate(
    *,
    planned_conditions: int = 1,
    planned_root_runs: int,
    planned_ai_units: int,
    provider_attempt_upper_bound: int,
    max_tokens: int,
    token_upper_bound: int | None = None,
    max_condition_root_runs: int | None = None,
    max_condition_ai_units: int | None = None,
    max_condition_provider_attempts: int | None = None,
) -> JsonObject:
    """按冻结 p95/envelope policy 计算 formal suite 的磁盘 forecast。"""

    inputs = {
        "planned_conditions": int(planned_conditions),
        "planned_root_runs": int(planned_root_runs),
        "planned_ai_units": int(planned_ai_units),
        "provider_attempt_upper_bound": int(provider_attempt_upper_bound),
        "max_tokens": int(max_tokens),
        "token_upper_bound": int(
            provider_attempt_upper_bound * max_tokens
            if token_upper_bound is None
            else token_upper_bound
        ),
        "max_condition_root_runs": int(
            planned_root_runs
            if max_condition_root_runs is None
            else max_condition_root_runs
        ),
        "max_condition_ai_units": int(
            planned_ai_units
            if max_condition_ai_units is None
            else max_condition_ai_units
        ),
        "max_condition_provider_attempts": int(
            provider_attempt_upper_bound
            if max_condition_provider_attempts is None
            else max_condition_provider_attempts
        ),
    }
    if any(value < 0 for value in inputs.values()):
        raise ValueError("formal disk estimate inputs must be non-negative")
    components = {
        "root_evidence_bytes": (
            inputs["planned_root_runs"] * int(_FORMAL_DISK_POLICY["p95_root_bytes"])
        ),
        "ai_unit_evidence_bytes": (
            inputs["planned_ai_units"]
            * int(_FORMAL_DISK_POLICY["p95_ai_unit_bytes"])
        ),
        "provider_attempt_envelope_bytes": (
            inputs["provider_attempt_upper_bound"]
            * int(_FORMAL_DISK_POLICY["p95_attempt_envelope_bytes"])
        ),
        "forecast_provider_attempt_payload_bytes": (
            inputs["provider_attempt_upper_bound"]
            * int(_FORMAL_DISK_POLICY["p95_provider_attempt_payload_bytes"])
        ),
        "model_execution_record_duplicate_bytes": (
            inputs["provider_attempt_upper_bound"]
            * int(_FORMAL_DISK_POLICY["p95_model_execution_record_bytes"])
            * int(
                _FORMAL_DISK_POLICY[
                    "model_execution_record_duplicate_multiplier"
                ]
            )
        ),
        "fixed_manifest_bytes": int(
            _FORMAL_DISK_POLICY["fixed_manifest_bytes"]
        ),
        "fixed_temp_bytes": int(_FORMAL_DISK_POLICY["fixed_temp_bytes"]),
    }
    max_condition_compaction_bytes = (
        inputs["max_condition_root_runs"]
        * int(_FORMAL_DISK_POLICY["p95_root_bytes"])
        + inputs["max_condition_ai_units"]
        * int(_FORMAL_DISK_POLICY["p95_ai_unit_bytes"])
        + inputs["max_condition_provider_attempts"]
        * int(_FORMAL_DISK_POLICY["p95_attempt_envelope_bytes"])
        + inputs["max_condition_provider_attempts"]
        * int(_FORMAL_DISK_POLICY["p95_provider_attempt_payload_bytes"])
        + inputs["max_condition_provider_attempts"]
        * int(_FORMAL_DISK_POLICY["p95_model_execution_record_bytes"])
        * int(
            _FORMAL_DISK_POLICY[
                "model_execution_record_duplicate_multiplier"
            ]
        )
    )
    return {
        "schema_version": "tokenshare.paper_disk_estimate.v3",
        "inputs": inputs,
        "policy": _json_copy(_FORMAL_DISK_POLICY),
        "components": components,
        "forecast_bytes": sum(components.values()),
        "theoretical_max_payload_bytes": (
            inputs["token_upper_bound"]
            * int(_FORMAL_DISK_POLICY["utf8_bytes_per_token"])
        ),
        "max_condition_compaction_bytes": max_condition_compaction_bytes,
    }


def _prepare_exp5_v3_endpoint_budget_specs(
    *,
    conditions: tuple[PaperExperimentCondition, ...],
    model_endpoint_cohort_preflight: JsonObject | None,
    token_upper_bound_by_endpoint_identity_digest: (
        Mapping[str, int] | None
    ),
) -> dict[str, JsonObject] | None:
    v3_conditions = tuple(
        condition
        for condition in conditions
        if condition.experiment_id == EXP5_EXPERIMENT_ID
        and condition.model_cohort_id == PAPER_MODEL_ENDPOINT_COHORT_V3_ID
    )
    preflight_is_v3 = (
        isinstance(model_endpoint_cohort_preflight, Mapping)
        and model_endpoint_cohort_preflight.get("cohort_id")
        == PAPER_MODEL_ENDPOINT_COHORT_V3_ID
    )
    preflight_is_planned = (
        preflight_is_v3
        and model_endpoint_cohort_preflight.get("status") == "planned"
    )
    if token_upper_bound_by_endpoint_identity_digest is None:
        if v3_conditions or preflight_is_planned:
            raise ValueError(
                "Exp5 v3 endpoint token ceiling mapping is required"
            )
        return None
    if not preflight_is_planned:
        raise ValueError(
            "Exp5 v3 endpoint token ceiling mapping requires a planned preflight"
        )

    normalized_mapping = _normalize_endpoint_token_ceiling_mapping(
        token_upper_bound_by_endpoint_identity_digest
    )
    specs = _exp5_v3_member_budget_specs(
        model_endpoint_cohort_preflight
    )
    expected_mapping = {
        str(specs[member_id]["model_endpoint_identity_digest"]): int(
            specs[member_id]["token_upper_bound_per_provider_attempt"]
        )
        for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    }
    if normalized_mapping != expected_mapping:
        raise ValueError(
            "Exp5 v3 endpoint token ceiling mapping drift: "
            f"expected keys={sorted(expected_mapping)}, "
            f"actual keys={sorted(normalized_mapping)}"
        )

    all_exp5_conditions = tuple(
        condition
        for condition in conditions
        if condition.experiment_id == EXP5_EXPERIMENT_ID
    )
    member_ids = {condition.cohort_member_id for condition in all_exp5_conditions}
    expected_member_ids = set(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    if member_ids != expected_member_ids:
        raise ValueError(
            "Exp5 v3 endpoint identity drift: condition member inventory "
            "must cover the exact four-member cohort"
        )
    for condition in all_exp5_conditions:
        member_id = condition.cohort_member_id
        if member_id not in specs:
            raise ValueError(
                "Exp5 v3 endpoint identity drift: unknown condition member"
            )
        spec = specs[member_id]
        expected_fields = {
            "schema_version": "tokenshare.paper_condition.v3",
            "model_policy": "fixed_entry",
            "model_cohort_id": spec["cohort_id"],
            "cohort_member_id": member_id,
            "provider_config_id": spec["provider_config_id"],
            "model_entry_id": spec["selected_entry_id"],
            "provider_family": spec["provider_family"],
            "provider_model_id": spec["provider_model_id"],
            "reasoning_profile_id": spec["reasoning_profile_id"],
            "model_cohort_digest": spec["model_cohort_digest"],
            "source_provider_config_digest": (
                spec["source_provider_config_digest"]
            ),
            "model_endpoint_identity_digest": (
                spec["model_endpoint_identity_digest"]
            ),
        }
        if any(
            getattr(condition, field_name) != expected_value
            for field_name, expected_value in expected_fields.items()
        ):
            raise ValueError(
                "Exp5 v3 endpoint identity drift between condition and "
                f"member plan: {condition.condition_id}"
            )
    return specs


def _normalize_endpoint_token_ceiling_mapping(
    raw_mapping: Mapping[str, int],
) -> dict[str, int]:
    if not isinstance(raw_mapping, Mapping):
        raise ValueError(
            "Exp5 v3 endpoint token ceiling mapping must be an object"
        )
    normalized: dict[str, int] = {}
    for endpoint_digest, token_ceiling in raw_mapping.items():
        if not isinstance(endpoint_digest, str) or not endpoint_digest.strip():
            raise ValueError(
                "Exp5 v3 endpoint token ceiling keys must be non-empty digests"
            )
        if type(token_ceiling) is not int or token_ceiling < 1:
            raise ValueError(
                "Exp5 v3 endpoint token ceiling must be a positive integer"
            )
        normalized[endpoint_digest] = token_ceiling
    return normalized


def _exp5_v3_member_budget_specs(
    preflight: Mapping[str, Any],
) -> dict[str, JsonObject]:
    expected_member_ids = tuple(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS)
    raw_expected_member_ids = preflight.get("expected_member_ids")
    if (
        not isinstance(raw_expected_member_ids, list)
        or tuple(raw_expected_member_ids) != expected_member_ids
    ):
        raise ValueError(
            "Exp5 v3 endpoint token ceiling member inventory drift"
        )
    member_plans = preflight.get("member_plans")
    if not isinstance(member_plans, Mapping) or set(member_plans) != set(
        expected_member_ids
    ):
        raise ValueError(
            "Exp5 v3 endpoint token ceiling member plan inventory drift"
        )
    cohort_digest = preflight.get("model_cohort_digest")
    if not isinstance(cohort_digest, str) or not cohort_digest:
        raise ValueError("Exp5 v3 endpoint identity drift: missing cohort digest")

    request_controls_snapshot = preflight.get("request_controls_snapshot")
    if not isinstance(request_controls_snapshot, Mapping):
        raise ValueError(
            "Exp5 v3 endpoint identity drift: missing request controls snapshot"
        )
    if preflight.get("request_controls_snapshot_digest") != digest_json(
        dict(request_controls_snapshot)
    ):
        raise ValueError(
            "Exp5 v3 endpoint identity drift: request controls snapshot digest"
        )

    specs: dict[str, JsonObject] = {}
    seen_endpoint_digests: set[str] = set()
    for member_id in expected_member_ids:
        raw_plan = member_plans[member_id]
        if not isinstance(raw_plan, Mapping):
            raise ValueError(
                "Exp5 v3 endpoint identity drift: member plan must be an object"
            )
        plan = dict(raw_plan)
        if plan.get("status") != "planned" or plan.get("blocked_reasons") not in (
            [],
            (),
        ):
            raise ValueError(
                "Exp5 v3 endpoint identity drift: member plan is not planned"
            )
        if (
            plan.get("cohort_id") != PAPER_MODEL_ENDPOINT_COHORT_V3_ID
            or plan.get("model_cohort_digest") != cohort_digest
            or plan.get("cohort_member_id") != member_id
        ):
            raise ValueError(
                "Exp5 v3 endpoint identity drift in member plan"
            )
        for field_name in (
            "provider_config_id",
            "selected_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
            "source_provider_config_digest",
            "model_endpoint_identity_digest",
        ):
            field_value = plan.get(field_name)
            if not isinstance(field_value, str) or not field_value:
                raise ValueError(
                    "Exp5 v3 endpoint identity drift: "
                    f"missing {field_name} for {member_id}"
                )

        endpoint_digest = str(plan["model_endpoint_identity_digest"])
        if endpoint_digest in seen_endpoint_digests:
            raise ValueError(
                "Exp5 v3 endpoint identity drift: endpoint digests must be unique"
            )
        seen_endpoint_digests.add(endpoint_digest)

        request_controls = plan.get("request_controls")
        if not isinstance(request_controls, Mapping):
            raise ValueError(
                "Exp5 v3 endpoint identity drift: missing member request controls"
            )
        comparable = request_controls.get("comparable")
        reasoning = request_controls.get("provider_specific_reasoning")
        if not isinstance(comparable, Mapping) or not isinstance(
            reasoning,
            Mapping,
        ):
            raise ValueError(
                "Exp5 v3 endpoint identity drift: invalid member request controls"
            )
        comparable_body = dict(comparable)
        reasoning_body = dict(reasoning)
        if (
            request_controls.get("comparable_digest")
            != digest_json(comparable_body)
            or request_controls.get("provider_specific_reasoning_digest")
            != digest_json(reasoning_body)
            or comparable_body != dict(request_controls_snapshot)
        ):
            raise ValueError(
                "Exp5 v3 endpoint identity drift: request controls digest"
            )

        max_tokens = comparable_body.get("max_tokens")
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError(
                "Exp5 v3 endpoint token ceiling requires positive max_tokens"
            )
        enable_thinking = reasoning_body.get("enable_thinking")
        if type(enable_thinking) is not bool:
            raise ValueError(
                "Exp5 v3 endpoint token ceiling requires explicit enable_thinking"
            )
        if enable_thinking:
            thinking_budget = reasoning_body.get("thinking_budget")
            if type(thinking_budget) is not int or thinking_budget < 1:
                raise ValueError(
                    "Exp5 v3 endpoint token ceiling requires a positive "
                    "thinking_budget"
                )
        else:
            if "thinking_budget" in reasoning_body:
                raise ValueError(
                    "Exp5 v3 endpoint token ceiling forbids thinking_budget "
                    "when thinking is disabled"
                )
            thinking_budget = 0

        pricing_snapshot = plan.get("pricing_snapshot")
        if not isinstance(pricing_snapshot, Mapping):
            raise ValueError(
                "Exp5 v3 pricing snapshot digest drift: snapshot is missing"
            )
        pricing_body = dict(pricing_snapshot)
        pricing_snapshot_digest = plan.get("pricing_snapshot_digest")
        if pricing_snapshot_digest != digest_json(pricing_body):
            raise ValueError(
                "Exp5 v3 pricing snapshot digest drift"
            )
        currency = pricing_body.get("currency")
        if not isinstance(currency, str) or not currency:
            raise ValueError("Exp5 v3 pricing snapshot requires a currency")
        input_rate_field = (
            "uncached_input_per_million_tokens"
            if "uncached_input_per_million_tokens" in pricing_body
            else "input_per_million_tokens"
        )
        if input_rate_field not in pricing_body:
            raise ValueError(
                "Exp5 v3 pricing snapshot requires an input token rate"
            )
        input_rate = _non_negative_pricing_rate(
            pricing_body[input_rate_field]
        )
        if "output_per_million_tokens" not in pricing_body:
            raise ValueError(
                "Exp5 v3 pricing snapshot requires an output token rate"
            )
        output_rate = _non_negative_pricing_rate(
            pricing_body["output_per_million_tokens"]
        )
        completion_and_thinking_tokens = max_tokens + thinking_budget
        token_ceiling = (
            EXP5_V3_PROMPT_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            + completion_and_thinking_tokens
        )
        cost_per_attempt = round(
            (
                EXP5_V3_PROMPT_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
                * input_rate
                + completion_and_thinking_tokens * output_rate
            )
            / 1_000_000,
            12,
        )
        specs[member_id] = {
            "cohort_id": plan["cohort_id"],
            "model_cohort_digest": plan["model_cohort_digest"],
            "cohort_member_id": member_id,
            "provider_config_id": plan["provider_config_id"],
            "selected_entry_id": plan["selected_entry_id"],
            "provider_family": plan["provider_family"],
            "provider_model_id": plan["provider_model_id"],
            "reasoning_profile_id": plan["reasoning_profile_id"],
            "source_provider_config_digest": (
                plan["source_provider_config_digest"]
            ),
            "model_endpoint_identity_digest": endpoint_digest,
            "request_controls": _json_copy(dict(request_controls)),
            "prompt_token_upper_bound_per_provider_attempt": (
                EXP5_V3_PROMPT_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
            ),
            "completion_token_upper_bound_per_provider_attempt": max_tokens,
            "thinking_token_upper_bound_per_provider_attempt": thinking_budget,
            "token_upper_bound_per_provider_attempt": token_ceiling,
            "pricing_snapshot": _json_copy(pricing_body),
            "pricing_snapshot_digest": pricing_snapshot_digest,
            "pricing_input_rate_field": input_rate_field,
            "input_per_million_tokens_for_upper_bound": input_rate,
            "output_per_million_tokens_for_upper_bound": output_rate,
            "cost_currency": currency,
            "cost_upper_bound_per_provider_attempt": cost_per_attempt,
        }
    return specs


def _non_negative_pricing_rate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Exp5 v3 pricing snapshot rates must be numeric")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(
            "Exp5 v3 pricing snapshot rates must be finite and non-negative"
        )
    return normalized


def _endpoint_aware_budget_totals(
    *,
    max_provider_attempts: int,
    token_upper_bound_per_provider_attempt: int,
    cost_upper_bound_per_provider_attempt: float,
    endpoint_budget_specs: dict[str, JsonObject] | None,
    endpoint_usage_by_member: dict[str, JsonObject] | None,
) -> tuple[int, float, JsonObject | None]:
    if endpoint_budget_specs is None:
        return (
            max_provider_attempts * token_upper_bound_per_provider_attempt,
            max_provider_attempts * cost_upper_bound_per_provider_attempt,
            None,
        )
    if endpoint_usage_by_member is None or set(endpoint_usage_by_member) != set(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    ):
        raise ValueError("Exp5 v3 endpoint identity drift in budget usage")

    member_subtotals: dict[str, JsonObject] = {}
    token_upper_bound_by_member: dict[str, int] = {}
    cost_upper_bound_by_member: dict[str, float] = {}
    pricing_snapshot_digest_by_member: dict[str, str] = {}
    endpoint_identity_digest_by_member: dict[str, str] = {}
    mapped_provider_attempts = 0
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS:
        spec = endpoint_budget_specs[member_id]
        usage = endpoint_usage_by_member[member_id]
        planned_ai_unit_count = int(usage["planned_ai_unit_count"])
        provider_attempt_upper_bound = int(
            usage["provider_attempt_upper_bound"]
        )
        mapped_provider_attempts += provider_attempt_upper_bound
        prompt_tokens = provider_attempt_upper_bound * int(
            spec["prompt_token_upper_bound_per_provider_attempt"]
        )
        completion_tokens = provider_attempt_upper_bound * int(
            spec["completion_token_upper_bound_per_provider_attempt"]
        )
        thinking_tokens = provider_attempt_upper_bound * int(
            spec["thinking_token_upper_bound_per_provider_attempt"]
        )
        member_token_upper_bound = provider_attempt_upper_bound * int(
            spec["token_upper_bound_per_provider_attempt"]
        )
        member_cost_upper_bound = round(
            provider_attempt_upper_bound
            * float(spec["cost_upper_bound_per_provider_attempt"]),
            12,
        )
        token_upper_bound_by_member[member_id] = member_token_upper_bound
        cost_upper_bound_by_member[member_id] = member_cost_upper_bound
        pricing_snapshot_digest_by_member[member_id] = str(
            spec["pricing_snapshot_digest"]
        )
        endpoint_identity_digest_by_member[member_id] = str(
            spec["model_endpoint_identity_digest"]
        )
        member_subtotals[member_id] = {
            "planned_ai_unit_count": planned_ai_unit_count,
            "provider_attempt_upper_bound": provider_attempt_upper_bound,
            "prompt_token_upper_bound": prompt_tokens,
            "completion_token_upper_bound": completion_tokens,
            "thinking_token_upper_bound": thinking_tokens,
            "token_upper_bound_per_provider_attempt": int(
                spec["token_upper_bound_per_provider_attempt"]
            ),
            "token_upper_bound": member_token_upper_bound,
            "cost_currency": spec["cost_currency"],
            "cost_upper_bound_per_provider_attempt": float(
                spec["cost_upper_bound_per_provider_attempt"]
            ),
            "cost_upper_bound": member_cost_upper_bound,
            "model_endpoint_identity_digest": (
                spec["model_endpoint_identity_digest"]
            ),
            "pricing_snapshot_digest": spec["pricing_snapshot_digest"],
        }

    scalar_fallback_attempts = max_provider_attempts - mapped_provider_attempts
    if scalar_fallback_attempts < 0:
        raise ValueError(
            "Exp5 v3 endpoint identity drift: mapped attempts exceed suite total"
        )
    scalar_fallback_token_upper_bound = (
        scalar_fallback_attempts * token_upper_bound_per_provider_attempt
    )
    scalar_fallback_cost_upper_bound = (
        scalar_fallback_attempts * cost_upper_bound_per_provider_attempt
    )
    token_upper_bound = scalar_fallback_token_upper_bound + sum(
        token_upper_bound_by_member.values()
    )
    cost_upper_bound = round(
        scalar_fallback_cost_upper_bound
        + sum(cost_upper_bound_by_member.values()),
        12,
    )
    currencies = {
        str(spec["cost_currency"])
        for spec in endpoint_budget_specs.values()
    }
    if len(currencies) != 1:
        raise ValueError(
            "Exp5 v3 pricing snapshot currencies must match for suite totals"
        )
    endpoint_budget_identity: JsonObject = {
        "schema_version": "tokenshare.paper_endpoint_budget_identity.v1",
        "model_cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "model_cohort_digest": endpoint_budget_specs[
            PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS[0]
        ]["model_cohort_digest"],
        "prompt_token_upper_bound_per_provider_attempt": (
            EXP5_V3_PROMPT_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT
        ),
        "token_upper_bound_by_endpoint_identity_digest": {
            str(endpoint_budget_specs[member_id][
                "model_endpoint_identity_digest"
            ]): int(endpoint_budget_specs[member_id][
                "token_upper_bound_per_provider_attempt"
            ])
            for member_id in PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
        },
        "endpoint_identity_digest_by_member": endpoint_identity_digest_by_member,
        "pricing_snapshot_digest_by_member": pricing_snapshot_digest_by_member,
        "token_upper_bound_by_member": token_upper_bound_by_member,
        "cost_upper_bound_by_member": cost_upper_bound_by_member,
        "member_token_cost_subtotals": member_subtotals,
        "cost_currency": next(iter(currencies)),
        "scalar_fallback_subtotal": {
            "provider_attempt_upper_bound": scalar_fallback_attempts,
            "token_upper_bound_per_provider_attempt": (
                token_upper_bound_per_provider_attempt
            ),
            "token_upper_bound": scalar_fallback_token_upper_bound,
            "cost_upper_bound_per_provider_attempt": (
                cost_upper_bound_per_provider_attempt
            ),
            "cost_upper_bound": scalar_fallback_cost_upper_bound,
        },
        "token_upper_bound": token_upper_bound,
        "cost_upper_bound": cost_upper_bound,
    }
    endpoint_budget_identity["endpoint_budget_identity_digest"] = digest_json(
        endpoint_budget_identity
    )
    return token_upper_bound, cost_upper_bound, endpoint_budget_identity


def _lean_matrix_budget_identity(lean_3x3_matrix: JsonObject) -> JsonObject:
    """预算只绑定冻结矩阵身份，不绑定每次 preflight 生成的运行期 artifact。"""

    task15_budget_input = lean_3x3_matrix.get("task15_budget_input")
    if not isinstance(task15_budget_input, dict):
        return _json_copy(lean_3x3_matrix)
    return _json_copy(
        {
            "schema_version": lean_3x3_matrix.get("schema_version"),
            "catalog_digest": lean_3x3_matrix.get("catalog_digest"),
            "matrix_digest": lean_3x3_matrix.get("matrix_digest"),
            "environment_digest": lean_3x3_matrix.get("environment_digest"),
            "oracle_package_digests": lean_3x3_matrix.get(
                "oracle_package_digests"
            ),
            "target_case_count": lean_3x3_matrix.get("target_case_count"),
            "task15_budget_input": task15_budget_input,
        }
    )


def _condition_replacement_policy(
    condition: PaperExperimentCondition,
) -> JsonObject:
    if condition.experiment_id == "exp3_real_ai_fault_recovery":
        worker_death = _exp3_worker_death_identity(condition)
        max_retries = (
            int(worker_death["dead_worker_count"]) + 1
            if worker_death is not None
            else 2
        )
        return {
            "max_retries": max_retries,
            "replacement_attempts_allowed": True,
            "source": (
                "worker_termination_policy"
                if worker_death is not None
                else "post_raw_fault_hook"
            ),
        }
    if condition.experiment_id == "exp4_real_ai_protocol_ablation":
        replacement_allowed = condition.ablation_mode != "NO_REQUEUE"
        return {
            "max_retries": 1,
            "replacement_attempts_allowed": replacement_allowed,
            "source": "exp4_five_mode_protocol_policy",
        }
    return {
        "max_retries": 0,
        "replacement_attempts_allowed": False,
        "source": "no_protocol_replacement",
    }


def _condition_replacement_reserve(
    *,
    condition: PaperExperimentCondition,
    condition_ai_units: int,
) -> int:
    policy = _condition_replacement_policy(condition)
    if policy["replacement_attempts_allowed"] is not True:
        return 0
    return condition_ai_units * int(policy["max_retries"])


def _exp3_worker_death_identity(
    condition: PaperExperimentCondition,
) -> JsonObject | None:
    if condition.experiment_id != "exp3_real_ai_fault_recovery":
        return None
    prefix = (
        "exp3_worker_death_factorization"
        if condition.domain == "factorization"
        else "exp3_worker_death_lean"
    )
    parts = condition.condition_id.split("__")
    if not parts or parts[0] != prefix:
        if condition.fault_type == "worker_death":
            raise ValueError("Experiment 3 worker-death condition ID is invalid")
        return None
    if len(parts) != 5:
        raise ValueError("Experiment 3 worker-death condition ID is invalid")
    dead_part = parts[2]
    repeat_part = parts[4]
    if not dead_part.startswith("dead") or not repeat_part.startswith("rep"):
        raise ValueError("Experiment 3 worker-death condition ID is invalid")
    try:
        dead_worker_count = int(dead_part.removeprefix("dead"))
        repeat_id = int(repeat_part.removeprefix("rep"))
    except ValueError as exc:
        raise ValueError(
            "Experiment 3 worker-death condition ID is invalid"
        ) from exc
    if dead_worker_count not in {1, 3} or repeat_id != condition.repeat_id:
        raise ValueError("Experiment 3 worker-death condition identity drift")
    return {
        "domain": condition.domain,
        "task_slice_key": parts[1],
        "repeat_id": repeat_id,
        "dead_worker_count": dead_worker_count,
    }


def _exp3_supporting_baseline_key(
    condition: PaperExperimentCondition,
) -> str | None:
    # 正式 Exp3 统一引用已经持久化的 Exp1 证据；这里仍校验
    # worker-death condition identity，但不再调度额外 no-kill baseline。
    _exp3_worker_death_identity(condition)
    return None


def _budget_cases_for_condition(
    cases: tuple[JsonObject, ...],
    *,
    condition: PaperExperimentCondition,
) -> tuple[JsonObject, ...]:
    if condition.domain != "lean_proof":
        return cases
    if condition.paper_difficulty not in {"simple", "medium_lemma_dag", "hard_frontier"}:
        return cases
    return tuple(case for case in cases if _is_checker_backed_lean_budget_case(case))


def _is_checker_backed_lean_budget_case(case: JsonObject) -> bool:
    if case.get("schema_version") == "tokenshare.paper_lean_case.v1":
        proof_ref = case.get("oracle_proof_ref")
        return (
            isinstance(proof_ref, dict)
            and proof_ref.get("preflight_status") == "passed"
            and bool(proof_ref.get("proof_source"))
        )
    oracle_ref = case.get("oracle_proof_package_ref")
    return (
        case.get("preflight_status") == "passed"
        and isinstance(oracle_ref, dict)
        and bool(oracle_ref.get("node_proof_sources"))
    )


def _validate_exp1_pilot_profile_body(body: JsonObject) -> None:
    if body.get("schema_version") != EXP1_PILOT_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported Exp1 pilot profile schema")
    if body.get("experiment_id") != EXP1_PILOT_EXPERIMENT_ID:
        raise ValueError("Exp1 pilot profile must select only Experiment 1")
    if body.get("domains") != ["factorization", "lean_proof"]:
        raise ValueError("Exp1 pilot domains must be factorization then lean_proof")
    _require_non_empty_string(body, "suite_id")
    if body["suite_id"] == "paper_v1_plan":
        raise ValueError("Exp1 pilot must use an independent suite_id")
    if int(body.get("worker_count", 0)) < 1:
        raise ValueError("Exp1 pilot worker_count must be >= 1")
    repeat_count = int(body.get("repeat_count", 0))
    if repeat_count < 1:
        raise ValueError("Exp1 pilot repeat_count must be >= 1")
    seed_family = body.get("seed_family")
    if (
        not isinstance(seed_family, list)
        or len(seed_family) != repeat_count
        or any(not isinstance(seed, int) for seed in seed_family)
    ):
        raise ValueError("Exp1 pilot seed_family must provide one integer per repeat")

    baseline = body.get("baseline_provider")
    if not isinstance(baseline, dict):
        raise ValueError("Exp1 pilot baseline_provider must be an object")
    for field_name in (
        "baseline_id",
        "provider_config_path",
        "provider_config_id",
        "selected_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
    ):
        _require_non_empty_string(baseline, field_name)

    request_policy = body.get("request_policy")
    if not isinstance(request_policy, dict):
        raise ValueError("Exp1 pilot request_policy must be an object")
    if int(request_policy.get("max_provider_attempts_per_ai_unit", 0)) < 1:
        raise ValueError("Exp1 pilot max provider attempts must be >= 1")
    if baseline.get("provider_family") == "deepseek":
        if "temperature" in request_policy or "top_p" in request_policy:
            raise ValueError("DeepSeek Exp1 pilot must omit temperature and top_p")
        if request_policy.get("thinking") != {"type": "enabled"}:
            raise ValueError("DeepSeek Exp1 pilot thinking must be enabled")
        if request_policy.get("reasoning_effort") != "high":
            raise ValueError("DeepSeek Exp1 pilot reasoning_effort must be high")
    elif float(request_policy.get("temperature", -1)) != 0.0:
        raise ValueError("Exp1 pilot temperature must be frozen at 0.0")
    if request_policy.get("stream") is not False:
        raise ValueError("Exp1 pilot stream must be frozen at false")
    domain_limits = request_policy.get("domain_limits")
    if not isinstance(domain_limits, dict) or set(domain_limits) != {
        "factorization",
        "lean_proof",
    }:
        raise ValueError("Exp1 pilot requires both domain request limits")
    for domain, limits in domain_limits.items():
        if not isinstance(limits, dict):
            raise ValueError(f"Exp1 pilot {domain} limits must be an object")
        for field_name in (
            "timeout_seconds",
            "prompt_token_upper_bound",
            "max_tokens",
        ):
            if int(limits.get(field_name, 0)) < 1:
                raise ValueError(f"Exp1 pilot {domain} {field_name} must be >= 1")

    disk_policy = body.get("disk_policy")
    if not isinstance(disk_policy, dict):
        raise ValueError("Exp1 pilot disk_policy must be an object")
    for field_name in (
        "base_suite_bytes",
        "per_root_bytes",
        "per_ai_unit_bytes",
    ):
        if int(disk_policy.get(field_name, 0)) < 1:
            raise ValueError(f"Exp1 pilot {field_name} must be >= 1")

    catalog_slice = body.get("catalog_slice")
    if not isinstance(catalog_slice, list) or not catalog_slice:
        raise ValueError("Exp1 pilot catalog_slice must be non-empty")
    case_ids: list[str] = []
    for selection in catalog_slice:
        if not isinstance(selection, dict):
            raise ValueError("Exp1 pilot catalog selection must be an object")
        for field_name in (
            "case_id",
            "domain",
            "paper_difficulty",
            "execution_policy",
        ):
            _require_non_empty_string(selection, field_name)
        case_ids.append(str(selection["case_id"]))
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Exp1 pilot case_id values must be unique")


def _validate_provider_config_against_profile(
    *,
    profile_body: JsonObject,
    source_config: AIAPIExecutorConfig,
) -> None:
    request_policy = dict(profile_body["request_policy"])
    defaults = source_config.defaults
    if int(defaults.get("max_provider_attempts", 0)) != int(
        request_policy["max_provider_attempts_per_ai_unit"]
    ):
        raise ValueError("baseline provider max_provider_attempts does not match profile")
    if source_config.provider_family == "deepseek":
        if "temperature" in defaults or "top_p" in defaults:
            raise ValueError("DeepSeek baseline provider must omit temperature and top_p")
    elif float(defaults.get("temperature", -1)) != float(
        request_policy["temperature"]
    ):
        raise ValueError("baseline provider temperature does not match profile")
    if defaults.get("stream") is not request_policy["stream"]:
        raise ValueError("baseline provider stream does not match profile")


def _selected_entry(pilot_profile: Exp1PilotProfile) -> AIAPIProviderEntry:
    selected_entry_id = pilot_profile.model_endpoint_identity.selected_entry_id
    for entry in pilot_profile.source_provider_config.entries:
        if entry.entry_id == selected_entry_id:
            return entry
    raise ValueError("Exp1 baseline selected entry is missing")


def _validate_selected_case(*, selection: JsonObject, case: JsonObject) -> None:
    if selection.get("domain") != _case_domain(case):
        raise ValueError(
            f"Exp1 pilot {selection['case_id']} domain does not match catalog"
        )
    if selection.get("paper_difficulty") != case.get("paper_difficulty"):
        raise ValueError(
            f"Exp1 pilot {selection['case_id']} paper_difficulty does not match catalog"
        )
    if selection.get("topic_family") != case.get("topic_family"):
        if selection.get("topic_family") is not None or case.get("topic_family") is not None:
            raise ValueError(
                f"Exp1 pilot {selection['case_id']} topic_family does not match catalog"
            )

    is_no_oracle_frontier = (
        _case_domain(case) == "lean_proof"
        and case.get("paper_difficulty") == "hard_frontier"
        and case.get("oracle_proof_package_ref") is None
    )
    execution_policy = selection.get("execution_policy")
    if is_no_oracle_frontier:
        if execution_policy != "structured_blocked":
            raise ValueError("Lean hard/frontier case without oracle must be structured_blocked")
        if case.get("preflight_status") not in {
            "structured_blocked",
            "frontier_stress",
        }:
            raise ValueError("Lean hard/frontier blocked case has invalid preflight status")
        return
    if execution_policy != "execute":
        raise ValueError("checker-backed pilot cases must use execution_policy=execute")
    if (
        _case_domain(case) == "lean_proof"
        and case.get("paper_difficulty") == "medium_lemma_dag"
        and (
            case.get("preflight_status") != "passed"
            or not isinstance(case.get("oracle_proof_package_ref"), dict)
        )
    ):
        raise ValueError("Lean medium pilot case must be checker-backed and preflight passed")


def _case_request_profile(
    *,
    ordinal: int,
    selection: JsonObject,
    case: JsonObject,
    request_policy: JsonObject,
    selected_entry: AIAPIProviderEntry,
    disk_policy: JsonObject,
    executor_requirements: JsonObject,
) -> JsonObject:
    domain = _case_domain(case)
    limits = dict(request_policy["domain_limits"][domain])
    catalog_expected_ai_units = estimated_ai_units_for_case(case)
    blocked = selection["execution_policy"] == "structured_blocked"
    ai_unit_count = 0 if blocked else catalog_expected_ai_units
    max_attempts_per_unit = int(
        request_policy["max_provider_attempts_per_ai_unit"]
    )
    provider_attempt_upper_bound = ai_unit_count * max_attempts_per_unit
    prompt_token_upper_bound = (
        provider_attempt_upper_bound * int(limits["prompt_token_upper_bound"])
    )
    completion_token_upper_bound = (
        provider_attempt_upper_bound * int(limits["max_tokens"])
    )
    token_upper_bound = prompt_token_upper_bound + completion_token_upper_bound
    pricing = selected_entry.pricing
    if not isinstance(pricing.get("currency"), str) or not pricing["currency"]:
        raise ValueError("Exp1 pilot budget requires a pricing currency")
    input_rate = float(
        pricing.get(
            "input_per_million_tokens",
            pricing.get("uncached_input_per_million_tokens"),
        )
    )
    output_rate = float(pricing["output_per_million_tokens"])
    if input_rate < 0 or output_rate < 0:
        raise ValueError("Exp1 pilot provider pricing must be non-negative")
    cost_upper_bound = round(
        prompt_token_upper_bound * input_rate / 1_000_000
        + completion_token_upper_bound * output_rate / 1_000_000,
        12,
    )
    return {
        "ordinal": ordinal,
        "case_id": case["case_id"],
        "domain": domain,
        "legacy_difficulty": case["difficulty"],
        "paper_difficulty": case.get("paper_difficulty"),
        "topic_family": case.get("topic_family"),
        "execution_status": "structured_blocked" if blocked else "executable",
        "blocked_reason": selection.get("blocked_reason") if blocked else None,
        "catalog_expected_ai_unit_count": catalog_expected_ai_units,
        "ai_unit_count": ai_unit_count,
        "provider_attempt_upper_bound": provider_attempt_upper_bound,
        "prompt_token_upper_bound": prompt_token_upper_bound,
        "completion_token_upper_bound": completion_token_upper_bound,
        "token_upper_bound": token_upper_bound,
        "cost_currency": pricing["currency"],
        "cost_upper_bound": cost_upper_bound,
        "wall_clock_upper_bound_seconds": (
            provider_attempt_upper_bound * int(limits["timeout_seconds"])
        ),
        "disk_upper_bound_bytes": (
            int(disk_policy["per_root_bytes"])
            + ai_unit_count * int(disk_policy["per_ai_unit_bytes"])
        ),
        "request_limits": {
            **limits,
            "max_provider_attempts_per_ai_unit": max_attempts_per_unit,
            "stream": request_policy["stream"],
            **{
                field_name: request_policy[field_name]
                for field_name in (
                    "temperature",
                    "top_p",
                    "enable_thinking",
                    "thinking",
                    "reasoning_effort",
                )
                if field_name in request_policy
            },
        },
        "executor_requirements": dict(executor_requirements),
        "split_profile": _deterministic_split_profile(case),
        "ai_unit_bindings": (
            []
            if blocked
            else build_case_ai_unit_bindings(
                case,
                include_request_artifacts=False,
                executor_requirements=executor_requirements,
            )
        ),
    }


def _budget_split_profile(
    *,
    case: JsonObject,
    condition: PaperExperimentCondition,
    exact_selection: JsonObject | None,
) -> JsonObject:
    if (
        condition.experiment_id == "exp2_real_ai_scalability"
        and condition.domain == "factorization"
    ):
        if (
            exact_selection is None
            or exact_selection.get("split_profile_id")
            != "factorization.exp2_contiguous_20way.v1"
        ):
            raise ValueError(
                "Experiment 2 budget requires frozen 20-way split profile"
            )
        case_counts = exact_selection.get("case_expected_ai_unit_counts")
        if not isinstance(case_counts, dict):
            raise ValueError(
                "Experiment 2 budget requires per-case AI-unit commitments"
            )
        case_id = str(case["case_id"])
        requested_child_count = case_counts.get(case_id)
        if requested_child_count != 20:
            raise ValueError(
                "Experiment 2 budget per-case AI-unit commitment drift"
            )
        partition = partition_candidate_ranges(
            target_n=case["target_n"],
            requested_child_count=20,
            max_children_per_unit=20,
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        return {
            "split_kind": "factorization.exp2_contiguous_20way.v1",
            "requested_child_count": 20,
            "actual_child_count": len(partition.ranges),
            "ai_unit_order": [
                f"range_{item.child_index}" for item in partition.ranges
            ],
            "ranges": [
                {
                    "range_start": item.range_start,
                    "range_end": item.range_end,
                }
                for item in partition.ranges
            ],
            "partition_params_digest": partition.params.params_digest,
            "ranges_digest": partition.coverage_proof.ranges_digest,
        }
    split_profile = _deterministic_split_profile(case)
    if exact_selection is not None:
        case_counts = exact_selection.get("case_expected_ai_unit_counts")
        if isinstance(case_counts, dict):
            expected = case_counts.get(str(case["case_id"]))
            if (
                expected is not None
                and expected != len(split_profile["ai_unit_order"])
            ):
                raise ValueError(
                    "frozen per-case AI-unit commitment does not match split"
                )
    return split_profile


def _deterministic_split_profile(case: JsonObject) -> JsonObject:
    if _case_domain(case) == "factorization":
        requested_child_count = int(case["split_params"]["requested_child_count"])
        partition = partition_candidate_ranges(
            target_n=case["target_n"],
            requested_child_count=requested_child_count,
            max_children_per_unit=max(1, requested_child_count),
            min_divisor=case["candidate_start"],
            max_divisor=case["candidate_end"],
        )
        return {
            "split_kind": case["split_params"]["strategy_id"],
            "requested_child_count": requested_child_count,
            "actual_child_count": len(partition.ranges),
            "ai_unit_order": [
                f"range_{item.child_index}" for item in partition.ranges
            ],
            "ranges": [
                {
                    "range_start": item.range_start,
                    "range_end": item.range_end,
                }
                for item in partition.ranges
            ],
            "partition_params_digest": partition.params.params_digest,
            "ranges_digest": partition.coverage_proof.ranges_digest,
        }

    if case["schema_version"] == "tokenshare.paper_lean_case.v1":
        count = int(case["expected_child_count"])
        return {
            "split_kind": case["expected_split_kind"],
            "actual_child_count": count,
            "ai_unit_order": [f"child_{index}" for index in range(count)],
            "topic_family_version": case.get("topic_family_version"),
        }

    nodes = [str(node["node_id"]) for node in case["lemma_graph"]["nodes"]]
    declared_order = case.get("merge_plan_shape", {}).get("dependency_order")
    order = (
        [str(node_id) for node_id in declared_order]
        if isinstance(declared_order, list)
        and len(declared_order) == len(nodes)
        and set(declared_order) == set(nodes)
        else nodes
    )
    return {
        "split_kind": case["expected_split_kind"],
        "actual_child_count": int(case["expected_ai_unit_count"]),
        "ai_unit_order": order,
        "dependency_edges": [dict(edge) for edge in case["dependency_edges"]],
        "construction_rule_id": case["construction_rule_id"],
        "proof_assembly_shape": case["proof_assembly_shape"],
    }


def _validate_budget_approval(
    *,
    plan_only: bool,
    approve_budget_digest: str | OfflineImplementationApproval | None,
    budget_digest: str,
    budget_approval_required: bool,
) -> None:
    if isinstance(approve_budget_digest, OfflineImplementationApproval):
        raise PaperBudgetApprovalError(
            "offline plan approval is not a paid execution receipt"
        )
    if not isinstance(budget_approval_required, bool):
        raise ValueError("budget_approval_required must be a bool")
    if (
        not plan_only
        and budget_approval_required
        and approve_budget_digest is None
    ):
        raise PaperBudgetApprovalError("budget approval digest is required")
    if approve_budget_digest is not None and approve_budget_digest != budget_digest:
        raise PaperBudgetApprovalError("budget digest mismatch")


def _budget_approval_record(
    *,
    budget_digest: str,
    approve_budget_digest: str | OfflineImplementationApproval | None,
    budget_approval_required: bool,
    budget_mode: str | None = None,
) -> JsonObject:
    if budget_mode == "unlimited":
        approval_mode = "explicit_unlimited"
        authorization_source = "cli"
    elif not budget_approval_required and approve_budget_digest is None:
        approval_mode = "user_bypassed"
        authorization_source = "project_policy"
    elif approve_budget_digest == budget_digest:
        approval_mode = "approved"
        authorization_source = "provided_digest"
    else:
        approval_mode = "awaiting_user_approval"
        authorization_source = "user_required"
    return {
        "approval_required": budget_approval_required,
        "approval_mode": approval_mode,
        "authorization_source": authorization_source,
        "budget_digest": budget_digest,
        "provided_approval_digest": approve_budget_digest,
    }


def _require_non_empty_string(body: JsonObject, field_name: str) -> None:
    value = body.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Exp1 pilot {field_name} must be a non-empty string")


def _case_domain(case: JsonObject) -> str:
    if case["schema_version"] == "tokenshare.paper_factorization_case.v1":
        return "factorization"
    return "lean_proof"


def _exact_selection_commitments(
    selections: tuple[JsonObject, ...] | list[JsonObject] | None,
    *,
    conditions: tuple[PaperExperimentCondition, ...],
) -> dict[str, JsonObject]:
    if selections is None:
        return {}
    records = list(selections)
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("frozen selections must be JSON objects")
    if not records or any("condition_id" not in record for record in records):
        return {}
    if len(records) != len(conditions):
        raise ValueError("frozen selections must align with conditions")
    result: dict[str, JsonObject] = {}
    for condition, record in zip(conditions, records, strict=True):
        if record.get("condition_id") != condition.condition_id:
            raise ValueError("frozen selection condition order drift")
        if record.get("condition_digest") != condition.condition_digest:
            raise ValueError("frozen selection condition digest drift")
        result[condition.condition_id] = record
    return result


def _condition_endpoint_identities(
    conditions: tuple[PaperExperimentCondition, ...],
) -> list[JsonObject]:
    fields = (
        "model_cohort_id",
        "cohort_member_id",
        "provider_config_id",
        "model_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "model_cohort_digest",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    )
    identities: list[JsonObject] = []
    seen: set[str] = set()
    for condition in conditions:
        identity = {
            field_name: getattr(condition, field_name)
            for field_name in fields
            if getattr(condition, field_name) is not None
        }
        identity_digest = digest_json(identity)
        if identity_digest in seen:
            continue
        seen.add(identity_digest)
        identities.append(identity)
    return identities


def _json_copy(body: Any) -> Any:
    return json.loads(json.dumps(body, ensure_ascii=False))


def budget_with_status(
    budget: PaperBudgetResult,
    status: PaperStatus,
) -> PaperBudgetResult:
    return replace(budget, status=status)
