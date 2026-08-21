"""Slim V2 原子命令入口；Task 3 仅实现只读 ``plan``。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from .profiles import PlanV1, build_plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenshare-slim-v2")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--profile", choices=("representative", "full"), required=True)
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--output-root")
    plan.add_argument("--representative-raw-response-p95-bytes", type=int)
    return parser


def _plan_payload(plan: PlanV1, run_id: str) -> dict[str, Any]:
    return {
        "profile_id": plan.profile_id,
        "run_id": run_id,
        "paper_root_count": plan.paper_root_count,
        "execution_root_count": plan.execution_root_count,
        "online_provider_call_upper": plan.online_provider_call_upper,
        "estimate": {
            "response_bytes": plan.estimated_response_bytes,
            "bytes": plan.estimate_bytes,
            "gib": plan.estimate_gib,
        },
        "hard_upper": {
            "response_bytes": plan.hard_response_bytes,
            "bytes": plan.hard_upper_bytes,
            "gib": plan.hard_upper_gib,
            "online_response_artifact_bytes": (
                plan.online_response_artifact_hard_upper_bytes
            ),
            "online_response_artifact_gib": (
                plan.online_response_artifact_hard_upper_gib
            ),
        },
        "per_root_free_space_margin": {
            "bytes": plan.per_root_free_space_margin_bytes,
            "gib": plan.per_root_free_space_margin_gib,
        },
        "experiments": {
            experiment_id: {
                "paper_root_count": item.paper_root_count,
                "reference_root_count": item.reference_root_count,
                "planned_first_attempt_ai_units": (
                    item.planned_first_attempt_ai_units
                ),
                "protocol_execution_attempt_upper": (
                    item.protocol_execution_attempt_upper
                ),
                "provider_call_upper": item.provider_call_upper,
            }
            for experiment_id, item in plan.experiments.items()
        },
        "exp3_references": {
            "planned_first_attempt_ai_units": (
                plan.exp3_reference_planned_first_attempt_ai_units
            ),
            "protocol_execution_attempt_upper": (
                plan.exp3_reference_protocol_execution_attempt_upper
            ),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        plan = build_plan(
            args.profile,
            representative_raw_response_p95_bytes=(
                args.representative_raw_response_p95_bytes
            ),
        )
        print(json.dumps(_plan_payload(plan, args.run_id), ensure_ascii=False))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
