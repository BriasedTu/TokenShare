"""Catch accidental compiler launches in ordinary offline verification."""

from __future__ import annotations

import os
import re
import sys
from pathlib import PureWindowsPath


ACTIVE_ENV = "TOKENSHARE_PYTEST_LEAN_GUARD_ACTIVE"
GUARD_ERROR = "Lean integration requires --run-lean-integration"
_COMPILERS = {"lean", "lake", "elan"}
_SHELLS = {"cmd", "powershell", "pwsh", "sh", "bash", "zsh"}
_SHELL_COMPILER = re.compile(
    r"(?:^|[\s\"'\\/;&|])(lean|lake|elan)(?:\.exe)?(?=$|[\s\"';&|])",
    re.IGNORECASE,
)
_installed = False


def activate() -> None:
    """The audit hook also covers imported Popen aliases; activation is reversible."""
    global _installed
    if not _installed:
        sys.addaudithook(_audit_process)
        _installed = True


def _program_name(value: object) -> str:
    if not isinstance(value, (str, bytes, os.PathLike)):
        return ""
    # PureWindowsPath also handles POSIX slash separators on non-Windows hosts.
    return PureWindowsPath(os.fsdecode(value).strip('"')).stem.lower()


def _audit_process(event: str, args: tuple) -> None:
    if os.environ.get(ACTIVE_ENV) != "1":
        return
    if event == "subprocess.Popen":
        executable, command = args[:2]
        if executable is None:
            # Windows delegates executable selection to CreateProcess and emits
            # a command-line string here, even when Popen received a list.
            if isinstance(command, (str, bytes)):
                match = re.match(r'''\s*(?:"([^"]+)"|'([^']+)'|(\S+))''', os.fsdecode(command))
                executable = next((part for part in match.groups() if part), "") if match else ""
            elif command:
                executable = command[0]
        name = _program_name(executable)
        if name in _COMPILERS:
            raise RuntimeError(GUARD_ERROR)
        if name in _SHELLS:
            command_text = (
                os.fsdecode(command) if isinstance(command, (str, bytes))
                else " ".join(os.fsdecode(part) for part in command)
            )
            if _SHELL_COMPILER.search(command_text):
                raise RuntimeError(GUARD_ERROR)
    elif event in {"os.exec", "os.posix_spawn"}:
        if _program_name(args[0]) in _COMPILERS:
            raise RuntimeError(GUARD_ERROR)
    elif event == "os.system":
        if _SHELL_COMPILER.search(os.fsdecode(args[0])):
            raise RuntimeError(GUARD_ERROR)
