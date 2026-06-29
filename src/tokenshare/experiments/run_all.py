"""CLI entrypoint for running the latest local experiment suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from tokenshare.executors.ai_api_local_config import DEFAULT_LOCAL_AI_API_CONFIG_PATH
from tokenshare.executors.ai_api_local_config import load_local_ai_api_config
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
    if config_path.exists():
        loaded_config_digest = load_local_ai_api_config(config_path).config_digest

    suite_report = run_phase8_default_suite(output_root=Path(args.output_root), seed=args.seed)
    if loaded_config_digest is not None:
        suite_report["local_ai_api_config_digest"] = loaded_config_digest
        Path(suite_report["suite_report_path"]).write_text(
            json.dumps(suite_report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(suite_report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
