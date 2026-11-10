"""OPTIONAL rescue tool. Not the primary fixture path.

Canonical fixtures are written by the provider wrappers during a live run with
ACQUISCOUT_CAPTURE_SEARCH=1 / ACQUISCOUT_CAPTURE_PLANNER=1.

This tool salvages LOSSY fixtures from a pre-bouncer checkpoint when capture was
forgotten. It depends on a prior run and uses get_state_history, never raw SQL.

Usage:
    python -m acquiscout.dev.freeze_from_checkpoint <thread_id>
"""

from __future__ import annotations

import sys
from typing import Any

from acquiscout.config import Settings, get_settings
from acquiscout.pipeline.discovery_graph import build_discovery_graph, sync_sqlite_checkpointer
from acquiscout.pipeline.nodes import DiscoveryDeps
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.fixtures import fixture_key, save_fixture
from acquiscout.sources.govfiles import GOVFILES_VERSION, GovFilesSearchProvider
from acquiscout.sources.planner import GroqPlanner
from acquiscout.sources.tavily import TAVILY_VERSION, TavilySearchProvider


def _raw_result(candidate: Any) -> dict[str, Any]:
    if candidate.provider == "tavily_web":
        return {
            "url": candidate.identifier,
            "title": candidate.title,
            "snippet": candidate.snippet,
        }
    return {
        "entity_id": candidate.identifier,
        "legal_name": candidate.title,
        "url": str(candidate.crawl_url) if candidate.crawl_url is not None else None,
    }


def _params(provider: str, settings: Any) -> dict[str, object]:
    if provider == "tavily_web":
        return {
            "max_results": settings.search_max_results,
            "search_depth": "basic",
        }
    return {"max_results": settings.search_max_results}


def freeze(thread_id: str, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    cache = SQLiteCache(settings.search_cache_db_path, settings.discovery_cache_ttl_hours * 3600)
    deps = DiscoveryDeps(
        settings=settings,
        planner=GroqPlanner(settings, cache=cache),
        tavily=TavilySearchProvider(settings, cache=cache),
        govfiles=GovFilesSearchProvider(settings, cache=cache),
        resolver=GovFilesSearchProvider(settings, cache=cache),
    )
    saver, conn = sync_sqlite_checkpointer(settings)
    try:
        graph = build_discovery_graph(deps, saver)
        config = {"configurable": {"thread_id": thread_id}}
        for snapshot in graph.get_state_history(config):
            if "bouncer" not in (snapshot.next or ()):
                continue
            candidates = snapshot.values.get("raw_search_results") or []
            written = 0
            for candidate in candidates:
                provider = candidate.provider
                version = TAVILY_VERSION if provider == "tavily_web" else GOVFILES_VERSION
                params = _params(provider, settings)
                batches: dict[str, list[dict[str, Any]]] = {}
                for hit in candidate.found_by:
                    key = fixture_key(provider, hit.query, params, version)
                    batches.setdefault(key, []).append(_raw_result(candidate))
                for key, results in batches.items():
                    save_fixture(
                        settings.dev_fixtures_dir,
                        "search",
                        provider,
                        key,
                        {"results": results},
                    )
                    written += 1
            return written
    finally:
        conn.close()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python -m acquiscout.dev.freeze_from_checkpoint <thread_id>",
            file=sys.stderr,
        )
        return 2
    written = freeze(argv[1])
    if written == 0:
        print(f"No pre-bouncer checkpoint found for thread {argv[1]}", file=sys.stderr)
        return 1
    print(f"Wrote {written} lossy fixture file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
