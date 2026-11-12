"""Async token-bucket rate limiter with an injectable clock."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class AsyncRateLimiter:
    def __init__(
        self,
        max_calls: int,
        period_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_calls <= 0 or period_seconds <= 0:
            raise ValueError("rate limiter requires positive limits")
        self._max_calls = max_calls
        self._period = period_seconds
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(max_calls)
        self._updated = clock()
        self._lock = asyncio.Lock()

    @property
    def rate_per_second(self) -> float:
        return self._max_calls / self._period

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(
            float(self._max_calls),
            self._tokens + elapsed * self.rate_per_second,
        )
        self._updated = now

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            self._refill(now)
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate_per_second
                await self._sleep(wait)
                now = self._clock()
                self._refill(now)
            self._tokens -= 1.0


__all__ = ["AsyncRateLimiter"]
