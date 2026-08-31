from __future__ import annotations

import json
from pathlib import Path
import urllib.request


def test_plan_prints_estimate_and_hard_upper_bytes_without_loading_secret_or_provider(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    from tokenshare.experiments import cli

    real_open = Path.open
    secret_reads = 0
    provider_calls = 0

    def guarded_open(path: Path, *args, **kwargs):
        nonlocal secret_reads
        if path.name == "ai_api_smoke.local.json":
            secret_reads += 1
            raise AssertionError("plan must not load the local secret config")
        return real_open(path, *args, **kwargs)

    def forbidden_urlopen(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("plan must not call a provider")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_urlopen)
    output_root = tmp_path / "must-not-be-created"

    exit_code = cli.main(
        [
            "plan",
            "--profile",
            "full",
            "--run-id",
            "plan-only",
            "--output-root",
            str(output_root),
            "--representative-raw-response-p95-bytes",
            str(256 * 1024),
        ]
    )
    rendered = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert rendered["profile_id"] == "full"
    assert rendered["run_id"] == "plan-only"
    assert rendered["estimate"]["bytes"] > 0
    assert rendered["estimate"]["gib"] == rendered["estimate"]["bytes"] / 1024**3
    assert rendered["hard_upper"]["bytes"] > rendered["estimate"]["bytes"]
    assert rendered["hard_upper"]["gib"] == rendered["hard_upper"]["bytes"] / 1024**3
    assert rendered["per_root_free_space_margin"]["bytes"] >= (
        4 * rendered["estimate"]["response_bytes"]
    )
    assert rendered["per_root_free_space_margin"]["gib"] == (
        rendered["per_root_free_space_margin"]["bytes"] / 1024**3
    )
    assert rendered["online_provider_call_upper"] == 6_762
    assert secret_reads == provider_calls == 0
    assert not output_root.exists()
