from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize("profile", ["representative", "full"])
@pytest.mark.parametrize(
    ("experiment", "command"),
    [
        ("exp1", "run"),
        ("exp2", "run"),
        ("exp3", "run"),
        ("exp4", "run"),
        ("exp5", "run"),
        ("all", "run-all"),
    ],
)
def test_build_cli_argv_maps_all_twelve_core_selections(
    profile: str,
    experiment: str,
    command: str,
    tmp_path: Path,
) -> None:
    from tokenshare.experiments.slim_v2.gui import build_cli_argv

    source = tmp_path / "source"
    argv = build_cli_argv(
        profile=profile,
        experiment=experiment,
        run_id="task6-run",
        output_root=tmp_path,
        source_run_dir=(source if experiment in {"exp2", "exp3", "exp4"} else None),
        resume=True,
    )

    assert argv[:4] == [
        sys.executable,
        "-m",
        "tokenshare.experiments.slim_v2.cli",
        command,
    ]
    assert argv[argv.index("--profile") + 1] == profile
    assert argv[argv.index("--run-id") + 1] == "task6-run"
    assert argv[argv.index("--output-root") + 1] == str(tmp_path)
    assert argv[-1] == "--resume"
    if command == "run":
        assert argv[argv.index("--experiment") + 1] == experiment
    else:
        assert "--experiment" not in argv
    if experiment in {"exp2", "exp3", "exp4"}:
        assert argv[argv.index("--source-run-dir") + 1] == str(source)
    else:
        assert "--source-run-dir" not in argv


@pytest.mark.parametrize("experiment", ["exp2", "exp3", "exp4"])
def test_build_cli_argv_rejects_missing_fixed_source(experiment: str) -> None:
    from tokenshare.experiments.slim_v2.gui import build_cli_argv

    with pytest.raises(ValueError, match="source_run_dir"):
        build_cli_argv(
            profile="representative",
            experiment=experiment,
            run_id="missing-source",
        )


def test_single_process_launcher_blocks_duplicate_until_exit() -> None:
    from tokenshare.experiments.slim_v2.gui import _SingleProcessLauncher

    processes: list[_FakeProcess] = []

    def popen(argv: list[str]) -> _FakeProcess:
        process = _FakeProcess(argv)
        processes.append(process)
        return process

    launcher = _SingleProcessLauncher(popen)
    launcher.start(["python", "-m", "tokenshare.experiments.slim_v2.cli", "run-all"])

    assert launcher.running
    with pytest.raises(RuntimeError, match="already running"):
        launcher.start(["must", "not", "start"])
    assert len(processes) == 1

    processes[0].returncode = 0
    assert launcher.poll() == 0
    assert not launcher.running
    launcher.start(["second", "allowed", "after", "exit"])
    assert len(processes) == 2


def test_opening_window_starts_no_cli_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenshare.experiments.slim_v2 import gui

    assert gui.__all__ == ("build_cli_argv", "main")
    root = _FakeRoot()
    tkinter = ModuleType("tkinter")
    tkinter.Tk = lambda: root  # type: ignore[attr-defined]
    tkinter.StringVar = _FakeVariable  # type: ignore[attr-defined]
    tkinter.BooleanVar = _FakeVariable  # type: ignore[attr-defined]
    tkinter.messagebox = SimpleNamespace(  # type: ignore[attr-defined]
        showerror=lambda *_args: pytest.fail("opening the window must not fail")
    )
    tkinter.ttk = SimpleNamespace(  # type: ignore[attr-defined]
        Frame=_FakeWidget,
        Label=_FakeWidget,
        Combobox=_FakeWidget,
        Entry=_FakeWidget,
        Checkbutton=_FakeWidget,
        Button=_FakeWidget,
    )
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    starts: list[list[str]] = []
    monkeypatch.setattr(
        gui._SingleProcessLauncher,
        "start",
        lambda _self, argv: starts.append(list(argv)),
    )

    assert gui.main() == 0
    assert root.mainloop_count == 1
    assert starts == []

    source = Path(gui.__file__).read_text(encoding="utf-8")
    for forbidden_import in (
        "from .runtime import",
        "from .provider import",
        "from .scenarios import",
        "from .reducer import",
    ):
        assert forbidden_import not in source


def test_launcher_is_only_a_checked_gui_entrypoint() -> None:
    repository = Path(__file__).parents[3]
    script = (repository / "run_slim_v2.cmd").read_text(encoding="utf-8").lower()

    assert 'set "pythonpath=' in script
    assert "%~dp0src" in script
    assert "where conda" in script
    assert script.count("conda run --no-capture-output -n tokenshare") == 3
    assert script.count(
        "conda run --no-capture-output -n tokenshare python -m "
        "tokenshare.experiments.slim_v2.gui"
    ) == 1
    assert "tokenshare.experiments.slim_v2.cli" not in script
    assert "slim_v2.runtime" not in script
    assert "for " not in script
    assert "goto " not in script
    assert "pip install" not in script


class _FakeProcess:
    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class _FakeVariable:
    def __init__(self, *, value: object) -> None:
        self._value = value

    def get(self) -> object:
        return self._value


class _FakeWidget:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def grid(self, **_kwargs: object) -> None:
        pass

    def grid_remove(self) -> None:
        pass

    def bind(self, *_args: object) -> None:
        pass

    def state(self, _states: list[str]) -> None:
        pass


class _FakeRoot:
    def __init__(self) -> None:
        self.mainloop_count = 0

    def title(self, _title: str) -> None:
        pass

    def resizable(self, _width: bool, _height: bool) -> None:
        pass

    def after(self, _delay_ms: int, _callback: object) -> None:
        pass

    def mainloop(self) -> None:
        self.mainloop_count += 1
