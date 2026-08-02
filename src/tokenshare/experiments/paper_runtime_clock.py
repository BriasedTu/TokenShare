"""论文 adapter 的 transport-aware lifecycle clock。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable

from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    ONLINE_REAL_TIME,
)


@dataclass(frozen=True)
class FixedLifecycleClock:
    """可跨 Windows process worker pickle 的固定时钟。"""

    timestamp: str

    def __call__(self) -> str:
        return self.timestamp


@dataclass(frozen=True)
class OnlineLifecycleClock:
    """显式绑定真实 wall/monotonic clock 的在线运行时钟。"""

    wall_clock: Callable[[], str]
    monotonic_clock: Callable[[], float]

    def __call__(self) -> str:
        return self.wall_clock()

    def monotonic_seconds(self) -> float:
        return self.monotonic_clock()


def runtime_lifecycle_clock(
    *,
    real_transport: bool,
    transport: Any,
    deterministic_now: str,
    provider_family: str | None = None,
    trace_delay_policy: str | None = None,
    sleeper: Callable[[float], None] | None = None,
    wall_clock: Callable[[], str] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
) -> Callable[[], str]:
    """真实 transport 使用 UTC 墙钟；离线/capturing fixture 保持固定时间。

    `occurred_at` 是业务观察时间，不能按 `event_seq` 假定全局单调：并发 worker
    可能先取时、后提交。`event_seq` 仅表示 ledger commit order；critical path
    必须按 dependency DAG 校验边的时间方向，并在矛盾时 fail closed。
    """

    if trace_delay_policy == LOGICAL_SOURCE_LATENCY_1X:
        if sleeper is not None:
            raise ValueError(
                "logical_source_latency_1x forbids every sleeper, including real and noop sleeper"
            )
        if real_transport:
            raise ValueError(
                "logical_source_latency_1x cannot be combined with real transport"
            )
        if wall_clock is not None or monotonic_clock is not None:
            raise ValueError(
                "logical_source_latency_1x is driven by the logical scheduler"
            )
        return FixedLifecycleClock(deterministic_now)
    if trace_delay_policy not in {None, ONLINE_REAL_TIME}:
        raise ValueError("unsupported runtime trace delay policy")

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
    if trace_delay_policy == ONLINE_REAL_TIME and (
        not real_transport or capturing
    ):
        raise ValueError("online_real_time requires non-capturing real transport")
    if real_transport and not capturing:
        if sleeper is not None:
            raise ValueError("online clock does not accept an injected sleeper")
        return OnlineLifecycleClock(
            wall_clock=wall_clock or _utc_now,
            monotonic_clock=monotonic_clock or monotonic,
        )
    if wall_clock is not None or monotonic_clock is not None or sleeper is not None:
        raise ValueError("offline fixed clock does not accept real-time callables")
    return FixedLifecycleClock(deterministic_now)


def _resolve_provider_transport(*, transport: Any, provider_family: str | None) -> Any:
    resolver = getattr(transport, "tokenshare_transport_for_provider", None)
    if not callable(resolver) or not provider_family:
        return transport
    resolved = resolver(provider_family)
    return transport if resolved is None else resolved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
