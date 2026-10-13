"""Explicit opt-in for real Lean/Lake work, including Python child processes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from verification import lean_integration_guard


_previous_environment: dict[str, str | None] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-lean-integration", action="store_true", default=False,
        help="Run marked integration tests using an already prepared local Lean cache.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "lean_integration: starts real Lean/Lake; requires --run-lean-integration",
    )
    for name in (lean_integration_guard.ACTIVE_ENV, "PYTHONPATH"):
        _previous_environment[name] = os.environ.get(name)
    os.environ[lean_integration_guard.ACTIVE_ENV] = (
        "0" if config.getoption("--run-lean-integration") else "1"
    )
    root = Path(__file__).resolve().parents[1]
    paths = [str(root / "verification"), str(root), str(root / "src")]
    paths.extend(item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item)
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    lean_integration_guard.activate()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-lean-integration"):
        return
    marker = pytest.mark.skip(reason="real Lean compilation requires --run-lean-integration")
    for item in items:
        if item.get_closest_marker("lean_integration") is not None:
            item.add_marker(marker)


def pytest_unconfigure(config: pytest.Config) -> None:
    for name, value in _previous_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _previous_environment.clear()
