from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "local" / "run_exp3_exp4_v3_smoke.ps1"


def test_exp3_exp4_v3_launcher_is_superseded_before_provider_access() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "EPD-027" in source
    assert "superseded" in source.lower()
    assert source.rstrip().endswith("exit 2")
    for forbidden in (
        "GetEnvironmentVariable",
        "ai_api_smoke.local.json",
        "Invoke-TokenShareNativeProcessWithLogs",
        "--real-transport",
    ):
        assert forbidden not in source
