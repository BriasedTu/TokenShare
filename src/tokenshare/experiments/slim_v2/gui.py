"""把本地窗口输入确定性转换为唯一 Slim V2 CLI 子进程。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from typing import Any


__all__ = ("build_cli_argv", "main")

_CLI_MODULE = "tokenshare.experiments.slim_v2.cli"
_PROFILES = ("representative", "full")
_EXPERIMENTS = ("exp1", "exp2", "exp3", "exp4", "exp5", "all")
_SOURCE_EXPERIMENTS = frozenset({"exp2", "exp3", "exp4"})


def build_cli_argv(
    *,
    profile: str,
    experiment: str,
    run_id: str,
    output_root: str | Path | None = None,
    source_run_dir: str | Path | None = None,
    resume: bool = False,
) -> list[str]:
    """生成窗口当前选择对应的同一 Slim V2 CLI argv。"""

    if profile not in _PROFILES:
        raise ValueError(f"unsupported profile: {profile}")
    if experiment not in _EXPERIMENTS:
        raise ValueError(f"unsupported experiment: {experiment}")
    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")

    normalized_source = str(source_run_dir).strip() if source_run_dir else ""
    if experiment in _SOURCE_EXPERIMENTS and not normalized_source:
        raise ValueError(f"source_run_dir is required for {experiment}")

    command = "run-all" if experiment == "all" else "run"
    argv = [sys.executable, "-m", _CLI_MODULE, command]
    if command == "run":
        argv.extend(("--experiment", experiment))
    argv.extend(("--profile", profile, "--run-id", normalized_run_id))
    if output_root and str(output_root).strip():
        argv.extend(("--output-root", str(output_root).strip()))
    if experiment in _SOURCE_EXPERIMENTS:
        argv.extend(("--source-run-dir", normalized_source))
    if resume:
        argv.append("--resume")
    return argv


class _SingleProcessLauncher:
    """只管理窗口拥有的一个 CLI 子进程句柄。"""

    def __init__(
        self,
        popen_factory: Callable[[Sequence[str]], Any] = subprocess.Popen,
    ) -> None:
        self._popen_factory = popen_factory
        self._process: Any | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self, argv: Sequence[str]) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                raise RuntimeError("Slim V2 CLI is already running")
            self._process = None
        self._process = self._popen_factory(list(argv))

    def poll(self) -> int | None:
        if self._process is None:
            return None
        return_code = self._process.poll()
        if return_code is not None:
            self._process = None
        return return_code


def _default_run_id() -> str:
    return datetime.now().strftime("slim-v2-%Y%m%d-%H%M%S")


def main() -> int:
    """打开薄参数窗口，并把启动动作交给唯一 CLI 子进程。"""

    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("TokenShare Slim V2")
    root.resizable(False, False)

    profile = tk.StringVar(value="representative")
    experiment = tk.StringVar(value="all")
    run_id = tk.StringVar(value=_default_run_id())
    output_root = tk.StringVar(value="")
    source_run_dir = tk.StringVar(value="")
    resume = tk.BooleanVar(value=False)

    frame = ttk.Frame(root, padding=12)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="Profile").grid(row=0, column=0, sticky="w", pady=3)
    ttk.Combobox(
        frame,
        textvariable=profile,
        values=_PROFILES,
        state="readonly",
        width=32,
    ).grid(row=0, column=1, sticky="ew", pady=3)

    ttk.Label(frame, text="Experiment").grid(row=1, column=0, sticky="w", pady=3)
    experiment_box = ttk.Combobox(
        frame,
        textvariable=experiment,
        values=_EXPERIMENTS,
        state="readonly",
        width=32,
    )
    experiment_box.grid(row=1, column=1, sticky="ew", pady=3)

    ttk.Label(frame, text="Run ID").grid(row=2, column=0, sticky="w", pady=3)
    ttk.Entry(frame, textvariable=run_id, width=35).grid(
        row=2, column=1, sticky="ew", pady=3
    )

    ttk.Label(frame, text="Output root (optional)").grid(
        row=3, column=0, sticky="w", pady=3
    )
    ttk.Entry(frame, textvariable=output_root, width=35).grid(
        row=3, column=1, sticky="ew", pady=3
    )

    source_label = ttk.Label(frame, text="Source run directory")
    source_entry = ttk.Entry(frame, textvariable=source_run_dir, width=35)

    ttk.Checkbutton(frame, text="Resume", variable=resume).grid(
        row=5, column=1, sticky="w", pady=3
    )

    launcher = _SingleProcessLauncher()

    def refresh_source(*_unused: object) -> None:
        if experiment.get() in _SOURCE_EXPERIMENTS:
            source_label.grid(row=4, column=0, sticky="w", pady=3)
            source_entry.grid(row=4, column=1, sticky="ew", pady=3)
        else:
            source_label.grid_remove()
            source_entry.grid_remove()

    def poll_process() -> None:
        return_code = launcher.poll()
        if launcher.running:
            root.after(200, poll_process)
            return
        start_button.state(["!disabled"])
        if return_code not in (None, 0):
            messagebox.showerror(
                "TokenShare Slim V2",
                f"Slim V2 CLI exited with code {return_code}.",
            )

    def start() -> None:
        try:
            argv = build_cli_argv(
                profile=profile.get(),
                experiment=experiment.get(),
                run_id=run_id.get(),
                output_root=output_root.get(),
                source_run_dir=source_run_dir.get(),
                resume=resume.get(),
            )
            launcher.start(argv)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("TokenShare Slim V2", str(exc))
            return
        start_button.state(["disabled"])
        root.after(200, poll_process)

    experiment_box.bind("<<ComboboxSelected>>", refresh_source)
    start_button = ttk.Button(frame, text="Start", command=start)
    start_button.grid(row=6, column=1, sticky="e", pady=(9, 0))
    refresh_source()

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
