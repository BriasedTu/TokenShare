"""CLI entrypoint for paper real-AI experiment planning and gated execution."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.experiments.paper_budget import (
    EXP1_PILOT_EXPERIMENT_ID,
    Exp1PilotProfile,
    PaperBudgetApprovalError,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_model_policy import (
    build_model_endpoint_cohort_preflight,
    load_model_endpoint_cohort,
    load_model_entry_map,
    load_provider_config_map,
)
from tokenshare.experiments.paper_models import PaperStatus, PaperSuiteResult
from tokenshare.experiments.paper_runner import (
    build_lean_3x3_matrix_plan,
    execute_exp1_pilot,
    expand_plan_conditions,
    normalize_experiment_ids,
)


DEFAULT_FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v1.jsonl")
DEFAULT_LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")
DEFAULT_LEAN_LEMMA_GRAPH_CATALOG = Path(
    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or run TokenShare paper real-AI experiments.",
    )
    parser.add_argument("--output-root", default="outputs/experiments/paper_v1")
    parser.add_argument("--experiments", default="exp1")
    parser.add_argument("--worker-levels", default="10")
    parser.add_argument("--optional-worker-levels", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed-family", default="1")
    parser.add_argument("--real-transport", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--baseline-entry-id", default=None)
    parser.add_argument("--provider-attempt-limit", type=int, default=None)
    parser.add_argument("--token-limit", type=int, default=None)
    parser.add_argument("--cost-limit", type=float, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--approve-budget-digest", default=None)
    parser.add_argument("--exp1-pilot-profile", default=None)
    parser.add_argument("--ai-api-config", default="local/ai_api_smoke.local.json")
    parser.add_argument("--model-cohort-file", default=None)
    parser.add_argument("--model-entry-map", default=None)
    parser.add_argument("--provider-config", action="append", default=[])
    args = parser.parse_args(argv)

    output_base = Path(args.output_root)
    output_base.mkdir(parents=True, exist_ok=True)
    output_root = output_base
    pilot_blocked_output_root: Path | None = None
    experiment_ids = normalize_experiment_ids(tuple(args.experiments.split(",")))
    pilot_profile = _load_cli_pilot_profile(
        profile_path=args.exp1_pilot_profile,
        output_root=output_base,
        experiment_ids=experiment_ids,
    )
    if args.exp1_pilot_profile is not None and pilot_profile is None:
        return 3
    if pilot_profile is not None:
        if args.pilot:
            output_root = output_base / str(pilot_profile.body["suite_id"])
            pilot_blocked_output_root = output_base / (
                f"{pilot_profile.body['suite_id']}_blocked"
            )
        pilot_cli_error = _pilot_cli_profile_error(
            pilot_profile=pilot_profile,
            experiment_ids=experiment_ids,
            worker_levels=_parse_int_tuple(args.worker_levels),
            optional_worker_levels=_parse_int_tuple(args.optional_worker_levels),
            repeats=args.repeats,
            seed_family=_parse_int_tuple(args.seed_family),
        )
        if pilot_cli_error is not None:
            _write_blocked_suite(
                output_root=pilot_blocked_output_root or output_root,
                experiment_ids=experiment_ids,
                failure_kind="pilot_profile_mismatch",
                message=pilot_cli_error,
                suite_id=f"{pilot_profile.body['suite_id']}_blocked",
            )
            return 3
    if args.pilot and pilot_profile is None:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_pilot_profile",
            message="--pilot requires --exp1-pilot-profile",
        )
        return 3
    if args.pilot and args.plan_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_mode",
            message="--pilot cannot be combined with --plan-only",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (args.resume or args.replay_only) and not args.pilot:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only require --pilot",
        )
        return 3
    if args.resume and args.replay_only:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_resume_mode",
            message="--resume and --replay-only are mutually exclusive",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if args.pilot and args.baseline_entry_id is None:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_baseline_entry_id",
            message="--baseline-entry-id is required for Exp1 pilot execution",
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    if (
        args.pilot
        and args.baseline_entry_id
        != pilot_profile.model_endpoint_identity.selected_entry_id
    ):
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="baseline_entry_mismatch",
            message=(
                "--baseline-entry-id does not match the approved Exp1 pilot entry"
            ),
            suite_id=f"{pilot_profile.body['suite_id']}_blocked",
        )
        return 3
    model_endpoint_cohort_preflight, model_cohort = (
        _model_endpoint_cohort_preflight_for_suite(
            experiment_ids=experiment_ids,
            model_cohort_file=args.model_cohort_file,
            model_entry_map=args.model_entry_map,
            provider_config_args=tuple(args.provider_config),
        )
    )

    if not args.plan_only and not args.real_transport:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_real_transport",
            message="formal paper runs require --real-transport",
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 1
    catalog_manifest = load_paper_catalogs(
        factorization_path=DEFAULT_FACTOR_CATALOG,
        lean_path=DEFAULT_LEAN_CATALOG,
        lean_lemma_graph_path=(
            DEFAULT_LEAN_LEMMA_GRAPH_CATALOG
            if DEFAULT_LEAN_LEMMA_GRAPH_CATALOG.exists()
            else None
        ),
    )
    lean_3x3_matrix = build_lean_3x3_matrix_plan(
        catalog_manifest=catalog_manifest,
    )
    conditions = (
        ()
        if pilot_profile is not None
        else expand_plan_conditions(
            catalog_manifest=catalog_manifest,
            experiment_ids=experiment_ids,
            worker_levels=_parse_int_tuple(args.worker_levels),
            repeats=args.repeats,
            seed_family=_parse_int_tuple(args.seed_family),
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        )
    )
    del model_cohort
    model_policy_preflight = None
    try:
        budget = (
            plan_exp1_pilot(
                catalog_manifest=catalog_manifest,
                pilot_profile=pilot_profile,
                plan_only=args.plan_only,
                approve_budget_digest=args.approve_budget_digest,
            )
            if pilot_profile is not None
            else plan_paper_suite(
                catalog_manifest=catalog_manifest,
                conditions=conditions,
                max_provider_attempts_per_ai_unit=1,
                token_upper_bound_per_provider_attempt=2048,
                cost_upper_bound_per_provider_attempt=0.01,
                plan_only=args.plan_only,
                lean_3x3_matrix=lean_3x3_matrix,
                model_policy_preflight=model_policy_preflight,
                model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
                approve_budget_digest=args.approve_budget_digest,
            )
        )
    except PaperBudgetApprovalError as exc:
        _write_blocked_suite(
            output_root=pilot_blocked_output_root or output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_budget_approval"
            if args.approve_budget_digest is None
            else "budget_digest_mismatch",
            message=str(exc),
            suite_id=(
                f"{pilot_profile.body['suite_id']}_blocked"
                if pilot_profile is not None
                else "paper_v1_blocked"
            ),
        )
        return 2

    if args.pilot:
        if not (args.resume or args.replay_only):
            try:
                _inject_exp1_pilot_api_key(
                    pilot_profile=pilot_profile,
                    local_config_path=Path(args.ai_api_config),
                )
            except (OSError, ValueError) as exc:
                _write_blocked_suite(
                    output_root=pilot_blocked_output_root or output_root,
                    experiment_ids=experiment_ids,
                    failure_kind="missing_real_api_key",
                    message=str(exc),
                    suite_id=f"{pilot_profile.body['suite_id']}_blocked",
                )
                return 1
        execution_result = execute_exp1_pilot(
            catalog_manifest=catalog_manifest,
            pilot_profile=pilot_profile,
            budget=budget,
            approved_budget_digest=str(args.approve_budget_digest),
            baseline_entry_id=str(args.baseline_entry_id),
            output_base=output_base,
            real_transport=args.real_transport,
            provider_attempt_limit=args.provider_attempt_limit,
            token_limit=args.token_limit,
            cost_limit=args.cost_limit,
            resume=args.resume,
            replay_only=args.replay_only,
        )
        output_root = Path(str(execution_result.to_dict()["output_root"]))
        _write_plan_artifacts(
            output_root=output_root,
            budget=budget.to_dict(),
            pilot_profile=pilot_profile.to_dict(),
            lean_3x3_matrix=lean_3x3_matrix,
        )
        print(
            json.dumps(
                execution_result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    _write_plan_artifacts(
        output_root=output_root,
        budget=budget.to_dict(),
        pilot_profile=(
            pilot_profile.to_dict() if pilot_profile is not None else None
        ),
        lean_3x3_matrix=lean_3x3_matrix,
    )
    if model_policy_preflight is not None:
        (output_root / "model_policy_plan.json").write_text(
            json.dumps(
                model_policy_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if model_endpoint_cohort_preflight is not None:
        (output_root / "model_endpoint_cohort_plan.json").write_text(
            json.dumps(
                model_endpoint_cohort_preflight,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    suite_status = (
        PaperStatus.BLOCKED
        if (
            model_endpoint_cohort_preflight is not None
            and model_endpoint_cohort_preflight.get("status") == "blocked"
        )
        else PaperStatus.PLANNED
    )
    suite = PaperSuiteResult(
        suite_id=(
            str(pilot_profile.body["suite_id"])
            if pilot_profile is not None
            else "paper_v1_plan"
        ),
        status=suite_status,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=budget.planned_conditions,
        run_count=budget.planned_root_runs,
        task_count=budget.planned_root_runs,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref={"path": "audit/paper_eligibility_report.json"},
        budget_ref=budget.to_dict(),
        metrics_refs=[],
        audit_refs=[
            {"path": "lean_3x3_matrix.json"},
            *(
                [{"path": "exp1_pilot_profile.json"}]
                if pilot_profile is not None
                else []
            ),
            *(
                [{"path": "model_policy_plan.json"}]
                if model_policy_preflight is not None
                else []
            ),
            *(
                [{"path": "model_endpoint_cohort_plan.json"}]
                if model_endpoint_cohort_preflight is not None
                else []
            ),
        ],
        error_summary=[],
        model_policy_preflight=model_policy_preflight,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _inject_exp1_pilot_api_key(
    *,
    pilot_profile: Exp1PilotProfile,
    local_config_path: Path,
) -> None:
    """只把匹配 provider/model 的本地 secret 注入批准 entry 的 env。"""

    selected_entry_id = pilot_profile.model_endpoint_identity.selected_entry_id
    approved_entries = [
        entry
        for entry in pilot_profile.source_provider_config.entries
        if entry.entry_id == selected_entry_id and entry.enabled
    ]
    if len(approved_entries) != 1:
        raise ValueError("approved Exp1 pilot entry is missing or disabled")
    approved_entry = approved_entries[0]
    if os.environ.get(approved_entry.api_key_env):
        return
    if not local_config_path.is_file():
        raise ValueError(
            f"Exp1 pilot local AI API config is missing: {local_config_path}"
        )

    local_config = load_local_ai_api_config(local_config_path)
    candidates = sorted(
        (
            entry
            for entry in local_config.entries
            if entry.enabled
            and local_config.provider_family
            == pilot_profile.model_endpoint_identity.provider_family
            and entry.model
            == pilot_profile.model_endpoint_identity.provider_model_id
            and entry.base_url == approved_entry.base_url
        ),
        key=lambda entry: entry.entry_id,
    )
    if not candidates:
        raise ValueError(
            "Exp1 pilot local config has no enabled key for the approved provider/model"
        )
    os.environ[approved_entry.api_key_env] = candidates[0].resolve_api_key()


def _load_cli_pilot_profile(
    *,
    profile_path: str | None,
    output_root: Path,
    experiment_ids: tuple[str, ...],
) -> Exp1PilotProfile | None:
    if profile_path is None:
        return None
    try:
        return load_exp1_pilot_profile(Path(profile_path))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="invalid_pilot_profile",
            message=str(exc),
            suite_id="paper_exp1_minimal_pilot_invalid_profile",
        )
        return None


def _pilot_cli_profile_error(
    *,
    pilot_profile: Exp1PilotProfile,
    experiment_ids: tuple[str, ...],
    worker_levels: tuple[int, ...],
    optional_worker_levels: tuple[int, ...],
    repeats: int,
    seed_family: tuple[int, ...],
) -> str | None:
    body = pilot_profile.body
    if experiment_ids != (EXP1_PILOT_EXPERIMENT_ID,):
        return "Exp1 pilot profile can only plan Experiment 1"
    if worker_levels != (int(body["worker_count"]),):
        return "CLI worker levels do not match the frozen pilot profile"
    if optional_worker_levels:
        return "Exp1 minimal pilot does not allow optional worker levels"
    if repeats != int(body["repeat_count"]):
        return "CLI repeats do not match the frozen pilot profile"
    if seed_family != tuple(int(seed) for seed in body["seed_family"]):
        return "CLI seed family does not match the frozen pilot profile"
    return None


def _write_blocked_suite(
    *,
    output_root: Path,
    experiment_ids: tuple[str, ...],
    failure_kind: str,
    message: str,
    suite_id: str = "paper_v1_blocked",
) -> None:
    suite = PaperSuiteResult(
        suite_id=suite_id,
        status=PaperStatus.BLOCKED,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=0,
        run_count=0,
        task_count=0,
        provider_attempt_count=0,
        total_tokens=0,
        total_cost_estimate=0.0,
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref=None,
        metrics_refs=[],
        audit_refs=[],
        error_summary=[{"failure_kind": failure_kind, "message": message}],
    )
    _write_suite(output_root, suite)
    print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))


def _write_suite(output_root: Path, suite: PaperSuiteResult) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "suite_manifest.json").write_text(
        json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_plan_artifacts(
    *,
    output_root: Path,
    budget: dict,
    pilot_profile: dict | None,
    lean_3x3_matrix: dict,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "run_budget.json").write_text(
        json.dumps(budget, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if pilot_profile is not None:
        (output_root / "exp1_pilot_profile.json").write_text(
            json.dumps(
                pilot_profile,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    (output_root / "lean_3x3_matrix.json").write_text(
        json.dumps(
            lean_3x3_matrix,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _parse_provider_config_args(values: tuple[str, ...]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--provider-config must use provider=path")
        provider_config_id, path = value.split("=", 1)
        provider_config_id = provider_config_id.strip()
        path = path.strip()
        if not provider_config_id or not path:
            raise ValueError("--provider-config must use provider=path")
        result[provider_config_id] = Path(path)
    return result


def _model_endpoint_cohort_preflight_for_suite(
    *,
    experiment_ids: tuple[str, ...],
    model_cohort_file: str | None,
    model_entry_map: str | None,
    provider_config_args: tuple[str, ...],
) -> tuple[dict | None, dict | None]:
    if "exp5_real_ai_model_endpoint_comparison" not in experiment_ids:
        return None, None
    if model_cohort_file is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-cohort-file is required for Experiment 5",
            ),
            None,
        )
    try:
        cohort = load_model_endpoint_cohort(Path(model_cohort_file))
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return _blocked_model_endpoint_cohort_preflight(message=str(exc)), None
    if model_entry_map is None:
        return (
            _blocked_model_endpoint_cohort_preflight(
                message="--model-entry-map is required for Experiment 5",
                cohort=cohort,
            ),
            cohort,
        )
    try:
        entry_map = load_model_entry_map(Path(model_entry_map))
        provider_configs = load_provider_config_map(
            _parse_provider_config_args(provider_config_args)
        )
        preflight = build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=provider_configs,
        )
        return preflight, cohort
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return (
            _blocked_model_endpoint_cohort_preflight(message=str(exc), cohort=cohort),
            cohort,
        )


def _blocked_model_endpoint_cohort_preflight(
    *,
    message: str,
    cohort: dict | None = None,
) -> dict:
    return {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "blocked",
        "paper_eligible_possible": False,
        "blocked_reason": "incomplete_model_cohort",
        "ineligibility_reasons": ["incomplete_model_cohort"],
        "message": message,
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": cohort.get("cohort_id") if isinstance(cohort, dict) else None,
        "model_cohort_digest": (
            cohort.get("model_cohort_digest") if isinstance(cohort, dict) else None
        ),
        "expected_member_ids": [
            "glm_5_2_siliconflow",
            "qwen3_6_27b_siliconflow",
            "gpt_5_6_sol_high_openai",
        ],
        "missing_members": [],
        "missing_provider_configs": [],
        "missing_entry_ids": [],
        "ineligible_members": [],
        "member_plans": {},
    }

if __name__ == "__main__":
    raise SystemExit(main())
