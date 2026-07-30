from __future__ import annotations

from pathlib import Path

import pytest

import tokenshare.experiments.run_ai_profile as ai_profile_cli
import tokenshare.experiments.run_all as run_all_cli
import tokenshare.experiments.run_factorization_500_ai as factorization_cli
import tokenshare.experiments.run_lean_ai_benchmark as lean_cli
import tokenshare.experiments.run_paper_experiments as paper_cli


def _external_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(root))
    return root


def test_run_all_defaults_to_external_experiment_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = _external_data_root(tmp_path, monkeypatch)
    observed: dict[str, Path] = {}

    def fake_suite(*, output_root: Path, seed: int) -> dict[str, object]:
        observed["output_root"] = output_root
        return {"suite_report_path": str(output_root / "phase8_suite_report.json")}

    monkeypatch.setattr(run_all_cli, "run_phase8_default_suite", fake_suite)

    assert run_all_cli.main(["--ai-api-config", str(tmp_path / "missing.json")]) == 0
    assert observed["output_root"] == data_root / "outputs" / "experiments"


def test_ai_profile_defaults_to_external_profile_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = _external_data_root(tmp_path, monkeypatch)
    observed: dict[str, Path] = {}

    def fake_suite(**kwargs: object) -> dict[str, object]:
        observed["output_root"] = Path(kwargs["output_root"])
        return {"status": "captured"}

    monkeypatch.setattr(ai_profile_cli, "run_ai_profile_suite", fake_suite)

    assert ai_profile_cli.main(["--ai-api-config", str(tmp_path / "missing.json")]) == 0
    assert observed["output_root"] == (
        data_root / "outputs" / "experiments" / "ai_profile"
    )


def test_factorization_benchmark_defaults_to_external_benchmark_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = _external_data_root(tmp_path, monkeypatch)
    observed: dict[str, Path] = {}

    def fake_suite(**kwargs: object) -> dict[str, object]:
        observed["output_root"] = Path(kwargs["output_root"])
        return {"status": "captured"}

    monkeypatch.setattr(
        factorization_cli,
        "run_factorization_500_ai_suite",
        fake_suite,
    )

    assert factorization_cli.main(
        ["--ai-api-config", str(tmp_path / "missing.json")]
    ) == 0
    assert observed["output_root"] == (
        data_root / "outputs" / "experiments" / "factorization_500_ai"
    )


def test_lean_benchmark_defaults_to_external_benchmark_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = _external_data_root(tmp_path, monkeypatch)
    observed: dict[str, Path] = {}

    def fake_suite(**kwargs: object) -> dict[str, object]:
        observed["output_root"] = Path(kwargs["output_root"])
        return {"status": "captured"}

    monkeypatch.setattr(lean_cli, "run_lean_ai_benchmark_suite", fake_suite)

    assert lean_cli.main(["--ai-api-config", str(tmp_path / "missing.json")]) == 0
    assert observed["output_root"] == (
        data_root / "outputs" / "experiments" / "lean_ai_50"
    )


def test_paper_parser_defaults_to_external_paper_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = _external_data_root(tmp_path, monkeypatch)

    args = paper_cli._build_argument_parser().parse_args([])

    assert paper_cli._paper_output_root(args.output_root) == (
        data_root / "outputs" / "experiments" / "paper_v1"
    )


def test_paper_explicit_output_root_bypasses_invalid_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = tmp_path / "paper-explicit"
    monkeypatch.setenv(
        "TOKENSHARE_DATA_ROOT",
        str(paper_cli.Path(__file__).resolve().parents[2] / "invalid"),
    )
    args = paper_cli._build_argument_parser().parse_args(
        ["--output-root", str(explicit)]
    )

    assert paper_cli._paper_output_root(args.output_root) == explicit
