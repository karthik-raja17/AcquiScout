"""LangGraph Studio entry point for the discovery graph shape."""

from __future__ import annotations

from typing import Any

from acquiscout.config import get_settings
from acquiscout.pipeline.discovery_graph import build_discovery_graph
from acquiscout.pipeline.nodes import DiscoveryDeps
from acquiscout.sources.govfiles import GovFilesSearchProvider
from acquiscout.sources.planner import GroqPlanner
from acquiscout.sources.tavily import TavilySearchProvider


def graph(config: Any = None) -> Any:
    settings = get_settings()
    govfiles = GovFilesSearchProvider(settings)
    deps = DiscoveryDeps(
        settings=settings,
        planner=GroqPlanner(settings),
        tavily=TavilySearchProvider(settings),
        govfiles=govfiles,
        resolver=govfiles,
    )
    return build_discovery_graph(deps)


__all__ = ["graph"]
