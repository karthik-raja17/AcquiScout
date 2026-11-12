from __future__ import annotations

import pytest

from acquiscout.config import Settings
from acquiscout.schemas import UsageRecord
from acquiscout.sources.usage import BudgetExceeded, UsageTracker


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "max_tavily_credits_per_run": 2,
        "max_govfiles_credits_per_run": 1,
        "groq_run_token_budget": 10,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def record(provider: str, credits: int = 0, tokens: int = 0) -> UsageRecord:
    return UsageRecord(
        run_id="run-1",
        provider=provider,
        operation="test",
        credits=credits,
        total_tokens=tokens,
    )


@pytest.mark.asyncio
async def test_tavily_budget_is_enforced_before_the_call() -> None:
    tracker = UsageTracker(make_settings())

    await tracker.check_tavily(1)
    await tracker.record(record("tavily_web", credits=1))
    await tracker.check_tavily(1)
    await tracker.record(record("tavily_web", credits=1))

    with pytest.raises(BudgetExceeded):
        await tracker.check_tavily(1)
    assert tracker.totals()["tavily_credits"] == 2


@pytest.mark.asyncio
async def test_govfiles_budget_is_enforced() -> None:
    tracker = UsageTracker(make_settings())
    await tracker.check_govfiles()
    await tracker.record(record("govfiles_registry", credits=1))

    with pytest.raises(BudgetExceeded):
        await tracker.check_govfiles()


@pytest.mark.asyncio
async def test_groq_budget_marks_over_budget() -> None:
    tracker = UsageTracker(make_settings())

    over = await tracker.record(record("groq", tokens=11))

    assert over is True
    assert tracker.totals()["groq_tokens"] == 11
    assert tracker.totals()["over_budget"] is True


@pytest.mark.asyncio
async def test_usage_totals_include_limits() -> None:
    tracker = UsageTracker(make_settings())

    totals = tracker.totals()

    assert totals["limits"]["tavily_credits"] == 2
    assert totals["limits"]["govfiles_credits"] == 1
    assert totals["limits"]["groq_tokens"] == 10
    assert totals["over_budget"] is False
