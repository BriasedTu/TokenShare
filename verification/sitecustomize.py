"""仅在 pytest tripwire 显式激活时为 Python 子进程安装网络保险丝。"""

from __future__ import annotations

import os
import socket
import urllib.request
from functools import wraps
from pathlib import Path
from typing import Any, Callable


ACTIVE_ENV = "TOKENSHARE_PYTEST_NETWORK_TRIPWIRE_ACTIVE"
COUNTER_ENV = "TOKENSHARE_PYTEST_NETWORK_TRIPWIRE_COUNTER"
TRIPWIRE_ERROR = "network tripwire: outbound provider access forbidden"
_GUARD_MARKER = "_tokenshare_tripwire_guard"
_WRAPPER_MARKER = "_tokenshare_tripwire_provider_wrapper"

_originals: list[tuple[object, str, object]] | None = None


def activate() -> bool:
    """安装一次 guard；返回本模块是否拥有本次安装。"""
    global _originals

    if os.environ.get(ACTIVE_ENV) != "1":
        return False
    if getattr(urllib.request.urlopen, _GUARD_MARKER, False):
        return False

    from tokenshare.executors.ai_api_transport import (
        UrlLibDeepSeekTransport,
        UrlLibOpenAITransport,
        UrlLibSiliconFlowTransport,
    )

    originals: list[tuple[object, str, object]] = []
    _replace(originals, socket, "create_connection", _outbound_guard)
    _replace(originals, socket.socket, "connect", _outbound_guard)
    _replace(originals, urllib.request, "urlopen", _outbound_guard)
    for transport_type in (
        UrlLibSiliconFlowTransport,
        UrlLibOpenAITransport,
        UrlLibDeepSeekTransport,
    ):
        original = transport_type.post_chat_completion
        _replace(
            originals,
            transport_type,
            "post_chat_completion",
            _provider_wrapper(original),
        )
    _originals = originals
    return True


def deactivate() -> None:
    """仅撤销由当前模块实例安装的 guard。"""
    global _originals

    if _originals is None:
        return
    for owner, name, original in reversed(_originals):
        setattr(owner, name, original)
    _originals = None


def _replace(
    originals: list[tuple[object, str, object]],
    owner: object,
    name: str,
    replacement: object,
) -> None:
    originals.append((owner, name, getattr(owner, name)))
    setattr(owner, name, replacement)


def _outbound_guard(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError(TRIPWIRE_ERROR)


setattr(_outbound_guard, _GUARD_MARKER, True)


def _provider_wrapper(original: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if getattr(urllib.request.urlopen, _GUARD_MARKER, False):
            _record_provider_attempt()
            raise RuntimeError(TRIPWIRE_ERROR)
        return original(*args, **kwargs)

    setattr(wrapped, _WRAPPER_MARKER, True)
    return wrapped


def _record_provider_attempt() -> None:
    counter_value = os.environ.get(COUNTER_ENV)
    if not counter_value:
        raise RuntimeError(TRIPWIRE_ERROR)
    counter_path = Path(counter_value)
    with counter_path.open("ab", buffering=0) as handle:
        handle.write(b"provider-attempt\n")


if os.environ.get(ACTIVE_ENV) == "1":
    activate()

# Direct Python children inherit the pytest compiler boundary independently of
# the network tripwire. Normal CLI execution does not set this variable.
if os.environ.get("TOKENSHARE_PYTEST_LEAN_GUARD_ACTIVE") == "1":
    from verification.lean_integration_guard import activate as activate_lean_guard

    activate_lean_guard()
