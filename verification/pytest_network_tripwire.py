"""离线 pytest 的父进程与 Python 子进程网络保险丝。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_VERIFICATION_ROOT = _ROOT / "verification"
_SRC_ROOT = _ROOT / "src"
for _import_root in (_VERIFICATION_ROOT, _ROOT, _SRC_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from verification import sitecustomize as tripwire_bootstrap


TRIPWIRE_ERROR = tripwire_bootstrap.TRIPWIRE_ERROR

_counter_temp: TemporaryDirectory[str] | None = None
_owns_counter = False
_activated_here = False
_previous_environment: dict[str, str | None] = {}


def pytest_sessionstart(session: pytest.Session) -> None:
    """在测试收集前激活当前进程，并把 bootstrap 显式传给子进程。"""
    global _activated_here, _counter_temp, _owns_counter, _previous_environment

    managed_names = (
        tripwire_bootstrap.ACTIVE_ENV,
        tripwire_bootstrap.COUNTER_ENV,
        "PYTHONPATH",
    )
    _previous_environment = {name: os.environ.get(name) for name in managed_names}

    inherited_counter = os.environ.get(tripwire_bootstrap.COUNTER_ENV)
    inherited_active = os.environ.get(tripwire_bootstrap.ACTIVE_ENV) == "1"
    if inherited_active and inherited_counter:
        counter_path = Path(inherited_counter)
        _owns_counter = False
    else:
        _counter_temp = TemporaryDirectory(prefix="tokenshare-network-tripwire-")
        counter_path = Path(_counter_temp.name) / "provider-attempts.log"
        counter_path.touch()
        _owns_counter = True

    os.environ[tripwire_bootstrap.ACTIVE_ENV] = "1"
    os.environ[tripwire_bootstrap.COUNTER_ENV] = str(counter_path)
    os.environ["PYTHONPATH"] = _child_pythonpath(
        os.environ.get("PYTHONPATH")
    )
    _activated_here = tripwire_bootstrap.activate()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    counter_path = Path(os.environ[tripwire_bootstrap.COUNTER_ENV])
    assert counter_path.is_file(), "network tripwire: provider counter is missing"
    provider_call_count = sum(
        1
        for line in counter_path.read_text(encoding="utf-8").splitlines()
        if line == "provider-attempt"
    )
    assert provider_call_count == 0, (
        "network tripwire: production provider call count must be zero "
        f"(observed {provider_call_count})"
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    global _activated_here, _counter_temp, _owns_counter

    if _activated_here:
        tripwire_bootstrap.deactivate()
        _activated_here = False
    for name, previous in _previous_environment.items():
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
    if _owns_counter and _counter_temp is not None:
        _counter_temp.cleanup()
    _counter_temp = None
    _owns_counter = False


def _child_pythonpath(existing_value: str | None) -> str:
    values = [str(_VERIFICATION_ROOT), str(_ROOT), str(_SRC_ROOT)]
    if existing_value:
        values.extend(
            item for item in existing_value.split(os.pathsep) if item
        )
    return os.pathsep.join(dict.fromkeys(values))
