"""论文 adapter 的 transport-aware lifecycle clock。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class FixedLifecycleClock:
    """可跨 Windows process worker pickle 的固定时钟。"""

    timestamp: str

    def __call__(self) -> str:
        return self.timestamp


def runtime_lifecycle_clock(
    *,
    real_transport: bool,
    transport: Any,
    deterministic_now: str,
    provider_family: str | None = None,
) -> Callable[[], str]:
    """真实 transport 使用 UTC 墙钟；离线/capturing fixture 保持固定时间。

    `occurred_at` 是业务观察时间，不能按 `event_seq` 假定全局单调：并发 worker
    可能先取时、后提交。`event_seq` 仅表示 ledger commit order；critical path
    必须按 dependency DAG 校验边的时间方向，并在矛盾时 fail closed。
    """

    resolved_transport = _resolve_provider_transport(
        transport=transport,
        provider_family=provider_family,
    )
    capturing = any(
        candidate is not None
        and getattr(candidate, "tokenshare_offline_capturing_transport", False)
        is True
        for candidate in (transport, resolved_transport)
    )
    if real_transport and not capturing:
        return _utc_now
    return FixedLifecycleClock(deterministic_now)


def _resolve_provider_transport(*, transport: Any, provider_family: str | None) -> Any:
    resolver = getattr(transport, "tokenshare_transport_for_provider", None)
    if not callable(resolver) or not provider_family:
        return transport
    resolved = resolver(provider_family)
    return transport if resolved is None else resolved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
