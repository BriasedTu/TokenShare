"""Plan-only budget calculation for paper real-AI experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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
    build_model_endpoint_identity,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    PaperBudgetResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_case_ai_unit_bindings,
)
from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges


class PaperBudgetApprovalError(ValueError):
    pass


EXP1_PILOT_PROFILE_SCHEMA_VERSION = "tokenshare.paper_exp1_pilot_profile.v1"
EXP1_PILOT_EXPERIMENT_ID = "exp1_real_ai_feasibility"
EXP1_BASELINE_COHORT_ID = "tokenshare.paper.exp1_baseline.v1"


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
    "exp5_real_ai_model_endpoint_comparison": 5,
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
    approve_budget_digest: str | None = None,
    budget_approval_required: bool = True,
) -> PaperBudgetResult:
    """按冻结 profile 复算最小 Exp1 pilot，不进行任何 provider 调用。"""

    profile_body = pilot_profile.to_dict()
    request_policy = dict(profile_body["request_policy"])
    disk_policy = dict(profile_body["disk_policy"])
    selected_entry = _selected_entry(pilot_profile)
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
    )


def plan_paper_suite(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    conditions: tuple[PaperExperimentCondition, ...] | list[PaperExperimentCondition],
    max_provider_attempts_per_ai_unit: int,
    token_upper_bound_per_provider_attempt: int,
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
    approve_budget_digest: str | None = None,
    budget_approval_required: bool = True,
) -> PaperBudgetResult:
    if max_provider_attempts_per_ai_unit < 1:
        raise ValueError("max_provider_attempts_per_ai_unit must be >= 1")
    if token_upper_bound_per_provider_attempt < 1:
        raise ValueError("token_upper_bound_per_provider_attempt must be >= 1")
    if cost_upper_bound_per_provider_attempt < 0:
        raise ValueError("cost_upper_bound_per_provider_attempt must be >= 0")
    condition_tuple = tuple(conditions)
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
    exp4_requeue_ai_unit_upper_bound = 0
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
        for case in cases:
            split_profile = _deterministic_split_profile(case)
            planned_ai_unit_ids = [
                str(ai_unit_id) for ai_unit_id in split_profile["ai_unit_order"]
            ]
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
        condition_ai_units = sum(
            estimated_ai_units_for_case(case) for case in cases
        )
        planned_root_runs += len(cases)
        planned_ai_units += condition_ai_units
        if (
            condition.experiment_id == "exp4_real_ai_protocol_ablation"
            and condition.ablation_mode != "NO_REQUEUE"
        ):
            exp4_requeue_ai_unit_upper_bound += condition_ai_units
    max_provider_attempts = (
        planned_ai_units * max_provider_attempts_per_ai_unit
        + exp4_requeue_ai_unit_upper_bound
    )
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
                "max_provider_attempts_per_ai_unit": max_provider_attempts_per_ai_unit,
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
            or {
                "max_provider_attempts": max_provider_attempts,
                "token_upper_bound": (
                    max_provider_attempts * token_upper_bound_per_provider_attempt
                ),
                "cost_upper_bound": (
                    max_provider_attempts * cost_upper_bound_per_provider_attempt
                ),
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
    }
    body: JsonObject = {
        "planned_experiments": sorted({item.experiment_id for item in condition_tuple}),
        "planned_conditions": len(condition_tuple),
        "planned_root_runs": planned_root_runs,
        "planned_ai_units": planned_ai_units,
        "max_provider_attempts": max_provider_attempts,
        "token_upper_bound": max_provider_attempts * token_upper_bound_per_provider_attempt,
        "cost_upper_bound": max_provider_attempts * cost_upper_bound_per_provider_attempt,
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
        disk_estimate={
            "bytes": max(4096, planned_root_runs * 2048 + planned_ai_units * 1024)
        },
        status=PaperStatus.PLANNED,
    )


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
    if float(request_policy.get("temperature", -1)) != 0.0:
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
    if float(defaults.get("temperature", -1)) != float(
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
    if pricing.get("currency") != "USD":
        raise ValueError("Exp1 pilot budget currently requires USD pricing")
    input_rate = float(pricing["input_per_million_tokens"])
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
            "temperature": request_policy["temperature"],
            "stream": request_policy["stream"],
        },
        "split_profile": _deterministic_split_profile(case),
        "ai_unit_bindings": (
            []
            if blocked
            else build_case_ai_unit_bindings(
                case,
                include_request_artifacts=False,
            )
        ),
    }


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
    approve_budget_digest: str | None,
    budget_digest: str,
    budget_approval_required: bool,
) -> None:
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
    approve_budget_digest: str | None,
    budget_approval_required: bool,
) -> JsonObject:
    if not budget_approval_required and approve_budget_digest is None:
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
