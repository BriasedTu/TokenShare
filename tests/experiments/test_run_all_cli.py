import json
from pathlib import Path

from tokenshare.experiments.run_all import main


def test_run_all_cli_runs_default_suite_and_writes_report(tmp_path: Path, capsys):
    output_root = tmp_path / "experiments"

    exit_code = main(["--output-root", str(output_root), "--seed", "5"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "phase8_suite_report.json" in captured.out
    suite_report = json.loads((output_root / "phase8_suite_report.json").read_text(encoding="utf-8"))
    assert suite_report["schema_version"] == "phase8.experiment_suite_report.v1"
    assert suite_report["passed_runs"] == 12
    assert suite_report["blocked_runs"] == 0
    assert any(
        "lean_decomposition_merge" in path for path in suite_report["run_manifest_paths"]
    )
