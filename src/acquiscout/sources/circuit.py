"""Per-host circuit breaker for outbound scraping."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    reason: str | None = None
    retry_after_seconds: float = 0.0
    probe: bool = False


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0
    probe_in_flight: bool = False


class HostCircuitBreaker:
    """Stop calling a host after repeated source-level failures.

    States are closed (normal), open (short-circuit), and half-open (one probe
    after the cooldown). The breaker is keyed by the target URL host, not by the
    Jina reader host.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 1,
        cooldown_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0 or cooldown_seconds <= 0:
            raise ValueError("circuit breaker requires positive limits")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def host_for(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    async def before(self, url: str) -> CircuitDecision:
        host = self.host_for(url)
        if not host:
            return CircuitDecision(allowed=False, reason="missing_host")
        async with self._lock:
            state = self._states.setdefault(host, _CircuitState())
            now = self._clock()
            if state.open_until > now:
                return CircuitDecision(
                    allowed=False,
                    reason="host_circuit_open",
                    retry_after_seconds=round(state.open_until - now, 3),
                )
            if state.open_until > 0:
                if state.probe_in_flight:
                    return CircuitDecision(
                        allowed=False,
                        reason="host_circuit_half_open",
                    )
                state.probe_in_flight = True
                return CircuitDecision(allowed=True, probe=True)
            return CircuitDecision(allowed=True)

    async def record(
        self,
        url: str,
        *,
        failed: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        host = self.host_for(url)
        if not host:
            return
        async with self._lock:
            state = self._states.setdefault(host, _CircuitState())
            if not failed:
                state.failures = 0
                state.open_until = 0.0
                state.probe_in_flight = False
                return
            state.failures += 1
            if state.probe_in_flight or state.failures >= self._failure_threshold:
                cooldown = max(self._cooldown_seconds, retry_after_seconds or 0.0)
                state.open_until = self._clock() + cooldown
                state.failures = 0
                state.probe_in_flight = False


__all__ = ["CircuitDecision", "HostCircuitBreaker"]
