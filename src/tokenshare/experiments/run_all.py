"""CLI entrypoint for running the latest local experiment suite."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from tokenshare.executors.ai_api_local_config import DEFAULT_LOCAL_AI_API_CONFIG_PATH
from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
from tokenshare.experiments.ai_profile import run_ai_profile_suite
from tokenshare.experiments.runner import run_phase8_default_suite


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run TokenShare Experiment 1-4 default suite and write local reports.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs/experiments",
        help="Directory for suite outputs, reports, copied event logs, and artifacts.",
    )
    parser.add_argument("--seed", type=int, default=1, help="Deterministic suite seed.")
    parser.add_argument(
        "--run-ai-profile",
        action="store_true",
        help="Also run the explicit AI profile suite under <output-root>/ai_profile.",
    )
    parser.add_argument(
        "--ai-api-config",
        default=str(DEFAULT_LOCAL_AI_API_CONFIG_PATH),
        help=(
            "Optional gitignored local JSON config. If it exists, API keys are loaded "
            "into this process only; deterministic suite cases still persist no secrets."
        ),
    )
    args = parser.parse_args(argv)

    config_path = Path(args.ai_api_config)
    loaded_config_digest: str | None = None
    loaded_config = None
    if config_path.exists():
        loaded_config = load_local_ai_api_config(config_path)
        loaded_config_digest = loaded_config.config_digest

    output_root = Path(args.output_root)
    suite_report = run_phase8_default_suite(output_root=output_root, seed=args.seed)
    if args.run_ai_profile:
        ai_profile_report = run_ai_profile_suite(
            output_root=output_root / "ai_profile",
            seed=args.seed,
            ai_api_config=loaded_config if _usable_ai_config(loaded_config) else None,
        )
        suite_report["ai_profile_suite_report_path"] = ai_profile_report[
            "suite_report_path"
        ]
    if loaded_config_digest is not None:
        suite_report["local_ai_api_config_digest"] = loaded_config_digest
    if loaded_config_digest is not None or args.run_ai_profile:
        Path(suite_report["suite_report_path"]).write_text(
            json.dumps(suite_report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(suite_report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _usable_ai_config(config) -> bool:
    if config is None:
        return False
    return any(entry.enabled and os.environ.get(entry.api_key_env) for entry in config.entries)


if __name__ == "__main__":
    raise SystemExit(main())
