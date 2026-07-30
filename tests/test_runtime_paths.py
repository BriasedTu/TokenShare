from pathlib import Path
from uuid import uuid4

import pytest

from tokenshare.runtime_paths import (
    data_root,
    default_data_root,
    diagnostic_outputs_root,
    experiment_outputs_root,
    repository_root,
    resolve_experiment_output_root,
    resolve_persisted_data_path,
    supervision_root,
)


def test_repository_root_returns_repository_root() -> None:
    expected = Path(__file__).resolve().parents[1]

    assert repository_root() == expected


def test_default_data_root_is_tokenshare_data_sibling() -> None:
    expected = Path(__file__).resolve().parents[2] / "TokenShareData"

    assert default_data_root() == expected


def test_data_root_uses_default_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKENSHARE_DATA_ROOT", raising=False)

    assert data_root() == default_data_root()


def test_data_root_resolves_absolute_override_outside_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    override = repo_root.parent / "TokenShareRuntimeData" / ".." / "TokenShareDataOverride"
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(override))

    assert data_root() == override.resolve()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("relative-data", "absolute"),
        (str(Path(__file__).resolve().parents[1]), "outside the repository"),
        (
            str(Path(__file__).resolve().parents[1] / "runtime-data"),
            "outside the repository",
        ),
    ],
)
def test_data_root_rejects_invalid_override(
    monkeypatch: pytest.MonkeyPatch,
    override: str,
    message: str,
) -> None:
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", override)

    with pytest.raises(ValueError, match=message):
        data_root()


def test_specialized_roots_are_derived_without_creating_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = repository_root().parent / f".tokenshare-runtime-paths-{uuid4().hex}"
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(candidate))
    assert not candidate.exists()

    assert experiment_outputs_root() == candidate / "outputs" / "experiments"
    assert diagnostic_outputs_root() == candidate / "outputs" / "diagnostics"
    assert supervision_root() == candidate / "local" / "supervision"
    assert not candidate.exists()


def test_explicit_output_root_bypasses_invalid_data_root_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit-output"
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(repository_root() / "invalid"))

    assert resolve_experiment_output_root(str(explicit), "paper_v1") == explicit


def test_missing_output_root_uses_external_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(candidate))

    assert resolve_experiment_output_root(None, "paper_v1") == (
        candidate / "outputs" / "experiments" / "paper_v1"
    )


def test_missing_persisted_repo_output_is_relocated_to_external_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    external_root = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(external_root))
    historical = repository_root() / "outputs" / "experiments" / "run-1" / "artifacts"

    assert resolve_persisted_data_path(historical) == (
        external_root / "outputs" / "experiments" / "run-1" / "artifacts"
    )


def test_relative_repository_output_path_is_relocated_to_external_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    external_root = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(external_root))

    assert resolve_persisted_data_path(
        "outputs/experiments/paper_v1/legacy/runs/example/artifacts"
    ) == (
        external_root
        / "outputs"
        / "experiments"
        / "paper_v1"
        / "legacy"
        / "runs"
        / "example"
        / "artifacts"
    )


def test_suite_relative_persisted_path_uses_explicit_suite_root(
    tmp_path: Path,
) -> None:
    suite_root = tmp_path / "migrated-suite"

    assert resolve_persisted_data_path(
        "runs/example/artifacts",
        relative_to=suite_root,
    ) == (suite_root / "runs" / "example" / "artifacts")


def test_existing_or_unrelated_persisted_path_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    external_root = (tmp_path / "TokenShareData").resolve()
    monkeypatch.setenv("TOKENSHARE_DATA_ROOT", str(external_root))
    existing = tmp_path / "existing"
    existing.mkdir()
    unrelated = repository_root() / "benchmarks" / "paper"

    assert resolve_persisted_data_path(existing) == existing
    assert resolve_persisted_data_path(unrelated) == unrelated
