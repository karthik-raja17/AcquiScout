from __future__ import annotations

import pytest

from acquiscout.sources.ratelimit import AsyncRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_rate_limiter_waits_for_the_next_token() -> None:
    clock = FakeClock()

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    limiter = AsyncRateLimiter(2, 60, clock=clock, sleep=sleep)

    await limiter.acquire()
    await limiter.acquire()
    assert clock.now == 0

    await limiter.acquire()
    assert clock.now == pytest.approx(30.0)


def test_rate_limiter_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        AsyncRateLimiter(0, 60)
    with pytest.raises(ValueError):
        AsyncRateLimiter(1, 0)


def test_rate_per_second() -> None:
    assert AsyncRateLimiter(18, 60).rate_per_second == pytest.approx(0.3)
