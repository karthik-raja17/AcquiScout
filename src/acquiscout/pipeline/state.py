"""Discovery graph state and reducers."""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from acquiscout.config import Settings
from acquiscout.pipeline.icp import build_icp
from acquiscout.schemas import (
    Candidate,
    CompanyCandidate,
    ResolvedEntity,
    SearchTask,
    UsageRecord,
)


def merge_search_results(
    old: list[Candidate] | None, new: list[Candidate] | None
) -> list[Candidate]:
    """Append parallel results; `None` is the explicit clear signal."""
    if new is None:
        return []
    if old is None:
        return list(new)
    return [*old, *new]


class DiscoveryState(TypedDict):
    run_id: str
    target_market: str
    target_sector: str
    icp: dict[str, Any]

    search_plan: list[SearchTask]
    raw_search_results: Annotated[list[Candidate] | None, merge_search_results]
    deduplicated_candidates: list[Candidate]
    directory_candidates: list[Candidate]
    resolved_entities: Annotated[list[ResolvedEntity], operator.add]
    enriched_entities: list[ResolvedEntity]
    evidenced_entities: list[ResolvedEntity]
    relevance_dropped: Annotated[list[dict[str, Any]], operator.add]
    company_candidates: list[CompanyCandidate]
    dropped_companies: list[dict[str, Any]]

    search_failure_count: Annotated[int, operator.add]
    provider_errors: Annotated[list[dict[str, Any]], operator.add]
    usage: Annotated[list[UsageRecord], operator.add]
    archive_errors: Annotated[list[str], operator.add]

    run_status: Literal["running", "completed", "aborted"]
    abort_reason: str | None


def initial_state(settings: Settings, run_id: str | None = None) -> DiscoveryState:
    return {
        "run_id": run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        "target_market": "tampa_bay",
        "target_sector": ",".join(sector.value for sector in settings.sectors),
        "icp": build_icp(settings),
        "search_plan": [],
        "raw_search_results": [],
        "deduplicated_candidates": [],
        "directory_candidates": [],
        "resolved_entities": [],
        "enriched_entities": [],
        "evidenced_entities": [],
        "relevance_dropped": [],
        "company_candidates": [],
        "dropped_companies": [],
        "search_failure_count": 0,
        "provider_errors": [],
        "usage": [],
        "archive_errors": [],
        "run_status": "running",
        "abort_reason": None,
    }


__all__ = ["DiscoveryState", "initial_state", "merge_search_results"]
