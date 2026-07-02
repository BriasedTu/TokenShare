"""CLI for the direct 500-number factorization AI benchmark."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from tokenshare.executors.ai_api_local_config import DEFAULT_LOCAL_AI_API_CONFIG_PATH
from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.experiments.factorization_500_ai import DEFAULT_MAX_TOKENS
from tokenshare.experiments.factorization_500_ai import DEFAULT_TIMEOUT_SECONDS
from tokenshare.experiments.factorization_500_ai import run_factorization_500_ai_suite


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run TokenShare direct AI factorization benchmark and report accuracy.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/experiments/factorization_500_ai",
        help="Directory for benchmark outputs.",
    )
    parser.add_argument("--count", type=int, default=500, help="Number of inputs.")
    parser.add_argument("--seed", type=int, default=1, help="Deterministic input seed.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="AI response token limit.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout.",
    )
    parser.add_argument(
        "--max-provider-attempts",
        type=int,
        default=1,
        help="Provider attempts per number; default keeps one AI call per task.",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=1,
        help="Concurrent benchmark workers; each input still writes an isolated run directory.",
    )
    parser.add_argument(
        "--ai-api-config",
        default=str(DEFAULT_LOCAL_AI_API_CONFIG_PATH),
        help="Gitignored local AI API config used for --real-transport.",
    )
    parser.add_argument(
        "--entry-id",
        action="append",
        default=None,
        help="Restrict benchmark to a configured AI API entry id; repeat for failover set.",
    )
    parser.add_argument(
        "--real-transport",
        action="store_true",
        help="Use real SiliconFlow transport instead of scripted local transport.",
    )
    args = parser.parse_args(argv)

    config = None
    config_path = Path(args.ai_api_config)
    if config_path.exists():
        config = load_local_ai_api_config(config_path)
    if args.real_transport and not _usable_ai_config(config):
        raise SystemExit("--real-transport requires a local config with at least one enabled API key")

    report = run_factorization_500_ai_suite(
        output_root=Path(args.output_root),
        count=args.count,
        seed=args.seed,
        ai_api_config=config if _usable_ai_config(config) else None,
        real_transport=args.real_transport,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout_seconds,
        max_provider_attempts=args.max_provider_attempts,
        entry_ids=args.entry_id,
        worker_count=args.worker_count,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _usable_ai_config(config) -> bool:
    if config is None:
        return False
    return any(entry.enabled and os.environ.get(entry.api_key_env) for entry in config.entries)


if __name__ == "__main__":
    raise SystemExit(main())
