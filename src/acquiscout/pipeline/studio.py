"""LangGraph Studio entry point for the discovery graph shape."""

from __future__ import annotations

from typing import Any

from acquiscout.config import get_settings
from acquiscout.pipeline.discovery_graph import build_discovery_graph
from acquiscout.pipeline.nodes import DiscoveryDeps
from acquiscout.services.company_url import CompanyUrlResolver
from acquiscout.sources.directory import DirectoryResolver
from acquiscout.sources.govfiles import GovFilesSearchProvider
from acquiscout.sources.planner import GroqPlanner
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.tavily import TavilySearchProvider


def graph(config: Any = None) -> Any:
    settings = get_settings()
    govfiles = GovFilesSearchProvider(settings)
    tavily = TavilySearchProvider(settings)
    directory = DirectoryResolver(settings)
    deps = DiscoveryDeps(
        settings=settings,
        planner=GroqPlanner(settings),
        tavily=tavily,
        govfiles=govfiles,
        resolver=govfiles,
        directory=directory,
        registry_evidence=RegistryEvidenceResolver(settings),
        company_url=CompanyUrlResolver(tavily),
    )
    return build_discovery_graph(deps)


__all__ = ["graph"]
