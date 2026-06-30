"""CLI entrypoint for the AI profile experiment suite."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from tokenshare.executors.ai_api_local_config import DEFAULT_LOCAL_AI_API_CONFIG_PATH
from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.experiments.ai_profile import run_ai_profile_suite


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run TokenShare AI profile suite for factorization outputs.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/experiments/ai_profile",
        help="Directory for AI profile outputs.",
    )
    parser.add_argument("--seed", type=int, default=1, help="Profile seed.")
    parser.add_argument(
        "--ai-api-config",
        default=str(DEFAULT_LOCAL_AI_API_CONFIG_PATH),
        help=(
            "Optional gitignored local JSON config. When present it is loaded with "
            "process-local secret injection; default run still uses scripted transport."
        ),
    )
    parser.add_argument(
        "--real-transport",
        action="store_true",
        help="Use real SiliconFlow transport with the supplied local config.",
    )
    args = parser.parse_args(argv)

    config = None
    config_path = Path(args.ai_api_config)
    if config_path.exists():
        config = load_local_ai_api_config(config_path)
    if args.real_transport and not _usable_ai_config(config):
        raise SystemExit("--real-transport requires a local config with at least one enabled API key")

    report = run_ai_profile_suite(
        output_root=Path(args.output_root),
        seed=args.seed,
        ai_api_config=config if _usable_ai_config(config) else None,
        real_transport=args.real_transport,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _usable_ai_config(config) -> bool:
    if config is None:
        return False
    return any(entry.enabled and os.environ.get(entry.api_key_env) for entry in config.entries)


if __name__ == "__main__":
    raise SystemExit(main())
