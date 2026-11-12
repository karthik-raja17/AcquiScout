"""Per-run usage tracking and budget enforcement."""

from __future__ import annotations

import asyncio
from typing import Any

from acquiscout.config import Settings
from acquiscout.schemas import UsageRecord

TAVILY_PROVIDERS = {"tavily", "tavily_web"}
GOVFILES_PROVIDERS = {"govfiles", "govfiles_registry"}
GROQ_PROVIDERS = {"groq", "planner"}


class BudgetExceeded(RuntimeError):
    pass


class UsageTracker:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._tavily_credits = 0
        self._govfiles_credits = 0
        self._groq_tokens = 0

    async def check_tavily(self, credits: int = 1) -> None:
        async with self._lock:
            if self._tavily_credits + credits > self._settings.max_tavily_credits_per_run:
                raise BudgetExceeded(
                    "Tavily credit budget exhausted: "
                    f"{self._tavily_credits}/{self._settings.max_tavily_credits_per_run}"
                )

    async def check_govfiles(self, credits: int = 1) -> None:
        async with self._lock:
            if self._govfiles_credits + credits > self._settings.max_govfiles_credits_per_run:
                raise BudgetExceeded(
                    "GovFiles credit budget exhausted: "
                    f"{self._govfiles_credits}/{self._settings.max_govfiles_credits_per_run}"
                )

    async def record(self, record: UsageRecord) -> bool:
        """Record usage and return True when a run budget is exceeded."""
        async with self._lock:
            if record.provider in TAVILY_PROVIDERS:
                self._tavily_credits += record.credits
            elif record.provider in GOVFILES_PROVIDERS:
                self._govfiles_credits += record.credits
            elif record.provider in GROQ_PROVIDERS:
                self._groq_tokens += record.total_tokens
            return self._over_budget()

    def _over_budget(self) -> bool:
        return (
            self._tavily_credits > self._settings.max_tavily_credits_per_run
            or self._govfiles_credits > self._settings.max_govfiles_credits_per_run
            or self._groq_tokens > self._settings.groq_run_token_budget
        )

    def totals(self) -> dict[str, Any]:
        return {
            "tavily_credits": self._tavily_credits,
            "govfiles_credits": self._govfiles_credits,
            "groq_tokens": self._groq_tokens,
            "limits": {
                "tavily_credits": self._settings.max_tavily_credits_per_run,
                "govfiles_credits": self._settings.max_govfiles_credits_per_run,
                "groq_tokens": self._settings.groq_run_token_budget,
            },
            "over_budget": self._over_budget(),
        }


__all__ = ["BudgetExceeded", "UsageTracker"]
