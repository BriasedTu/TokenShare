"""CLI entrypoint for paper real-AI experiment planning and gated execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from tokenshare.experiments.paper_budget import PaperBudgetApprovalError, plan_paper_suite
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
    parser.add_argument("--approve-budget-digest", default=None)
    parser.add_argument("--ai-api-config", default="local/ai_api_smoke.local.json")
    parser.add_argument("--model-cohort-file", default=None)
    parser.add_argument("--model-entry-map", default=None)
    parser.add_argument("--provider-config", action="append", default=[])
    args = parser.parse_args(argv)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    experiment_ids = normalize_experiment_ids(tuple(args.experiments.split(",")))
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
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_real_transport",
            message="formal paper runs require --real-transport",
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
    conditions = expand_plan_conditions(
        catalog_manifest=catalog_manifest,
        experiment_ids=experiment_ids,
        worker_levels=_parse_int_tuple(args.worker_levels),
        repeats=args.repeats,
        seed_family=_parse_int_tuple(args.seed_family),
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
    )
    del model_cohort
    model_policy_preflight = None
    try:
        budget = plan_paper_suite(
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
    except PaperBudgetApprovalError as exc:
        _write_blocked_suite(
            output_root=output_root,
            experiment_ids=experiment_ids,
            failure_kind="missing_budget_approval"
            if args.approve_budget_digest is None
            else "budget_digest_mismatch",
            message=str(exc),
        )
        return 2

    budget_path = output_root / "run_budget.json"
    budget_path.write_text(
        json.dumps(budget.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
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
        suite_id="paper_v1_plan",
        status=suite_status,
        output_root=output_root.as_posix(),
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:00Z",
        experiment_ids=list(experiment_ids),
        condition_count=len(conditions),
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


def _write_blocked_suite(
    *,
    output_root: Path,
    experiment_ids: tuple[str, ...],
    failure_kind: str,
    message: str,
) -> None:
    suite = PaperSuiteResult(
        suite_id="paper_v1_blocked",
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
    (output_root / "suite_manifest.json").write_text(
        json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
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
