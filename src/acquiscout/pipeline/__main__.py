"""Run the discovery slice: python -m acquiscout.pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from acquiscout.config import get_settings
from acquiscout.pipeline.discovery_graph import run_discovery, sqlite_checkpointer
from acquiscout.pipeline.export import export_run
from acquiscout.pipeline.nodes import DiscoveryDeps
from acquiscout.pipeline.runlog import append_discovery_run
from acquiscout.services.company_url import CompanyUrlResolver
from acquiscout.sources.archive import RunArchive
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.directory import DirectoryResolver
from acquiscout.sources.govfiles import GovFilesSearchProvider
from acquiscout.sources.planner import GroqPlanner
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.tavily import TavilySearchProvider
from acquiscout.sources.usage import UsageTracker


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the discovery subgraph")
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write the completed run to data/generated/discovery/<run_id>.json",
    )
    parser.add_argument("--export-dir", type=Path, default=None)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or [])
    settings = get_settings()
    cache = SQLiteCache(settings.search_cache_db_path, settings.discovery_cache_ttl_hours * 3600)
    planner = GroqPlanner(settings, cache=cache)
    tavily = TavilySearchProvider(settings, cache=cache)
    govfiles = GovFilesSearchProvider(settings, cache=cache)
    directory = DirectoryResolver(settings, cache=cache)
    registry_evidence = RegistryEvidenceResolver(settings)
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive = RunArchive(settings.discovery_archive_dir, run_id)
    usage = UsageTracker(settings)
    deps = DiscoveryDeps(
        settings=settings,
        planner=planner,
        tavily=tavily,
        govfiles=govfiles,
        resolver=govfiles,
        directory=directory,
        registry_evidence=registry_evidence,
        company_url=CompanyUrlResolver(tavily),
        archive=archive,
        usage=usage,
    )
    saver, conn = await sqlite_checkpointer(settings)
    try:
        final = await run_discovery(deps, checkpointer=saver, run_id=run_id)
    finally:
        await planner.aclose()
        await tavily.aclose()
        await govfiles.aclose()
        await directory.aclose()
        await registry_evidence.aclose()
        await conn.close()

    log_path = append_discovery_run(final, settings)
    print(f"run_id={final['run_id']}")
    print(f"status={final['run_status']}")
    if final["abort_reason"]:
        print(f"abort_reason={final['abort_reason']}")
    print(f"plan={len(final['search_plan'])}")
    print(f"failures={final['search_failure_count']}")
    print(f"candidates={len(final['deduplicated_candidates'])}")
    print(f"checkpoint={settings.checkpoint_db_path}")
    print(f"run_log={log_path}")
    print(f"archive={archive.directory}")
    print(f"usage={json.dumps(usage.totals())}")

    if args.export:
        exported = export_run(final, settings, args.export_dir)
        if exported is None:
            print(f"export=skipped (status={final['run_status']})")
        else:
            print(f"export={exported}")

    if final["provider_errors"]:
        print("provider_errors=" + json.dumps(final["provider_errors"], indent=2))
    return 0 if final["run_status"] != "aborted" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
