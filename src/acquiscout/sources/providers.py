"""Search and planner provider contracts."""

from __future__ import annotations

from typing import Any, Protocol

from acquiscout.schemas import Candidate, SearchTask


class ProviderError(RuntimeError):
    pass


class PlannerError(RuntimeError):
    pass


class SearchProvider(Protocol):
    name: str

    async def search(self, task: SearchTask) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


class Planner(Protocol):
    async def plan(self, icp: dict[str, Any], hints: dict[str, Any]) -> list[SearchTask]: ...

    async def aclose(self) -> None: ...


class RegistryResolver(Protocol):
    async def resolve(self, candidate: Candidate) -> str | None: ...


__all__ = [
    "Planner",
    "PlannerError",
    "ProviderError",
    "RegistryResolver",
    "SearchProvider",
]
