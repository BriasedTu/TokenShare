"""CLI entrypoint for paper real-AI experiment planning and gated execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from tokenshare.experiments.paper_budget import PaperBudgetApprovalError, plan_paper_suite
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import PaperStatus, PaperSuiteResult
from tokenshare.experiments.paper_runner import (
    expand_plan_conditions,
    normalize_experiment_ids,
)


DEFAULT_FACTOR_CATALOG = Path("benchmarks/paper/factorization_catalog.v1.jsonl")
DEFAULT_LEAN_CATALOG = Path("benchmarks/paper/lean_catalog.v1.jsonl")


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
    parser.add_argument("--strong-entry-id", default=None)
    parser.add_argument("--weak-entry-id", default=None)
    args = parser.parse_args(argv)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    experiment_ids = normalize_experiment_ids(tuple(args.experiments.split(",")))

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
    )
    conditions = expand_plan_conditions(
        catalog_manifest=catalog_manifest,
        experiment_ids=experiment_ids,
        worker_levels=_parse_int_tuple(args.worker_levels),
        repeats=args.repeats,
        seed_family=_parse_int_tuple(args.seed_family),
    )
    try:
        budget = plan_paper_suite(
            catalog_manifest=catalog_manifest,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=2048,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=args.plan_only,
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
    suite = PaperSuiteResult(
        suite_id="paper_v1_plan",
        status=PaperStatus.PLANNED,
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
        audit_refs=[],
        error_summary=[],
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


if __name__ == "__main__":
    raise SystemExit(main())
