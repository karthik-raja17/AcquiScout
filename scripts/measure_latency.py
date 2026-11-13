"""Measure per-node discovery latency with live providers.

Usage:
    python scripts/measure_latency.py
    python scripts/measure_latency.py --output data/generated/latency
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acquiscout.config import get_settings
from acquiscout.pipeline.discovery_graph import build_discovery_graph, memory_checkpointer
from acquiscout.pipeline.nodes import DiscoveryDeps, manifest_payload
from acquiscout.pipeline.state import initial_state
from acquiscout.services.company_url import CompanyUrlResolver
from acquiscout.sources.archive import RunArchive
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.directory import DirectoryResolver
from acquiscout.sources.govfiles import GovFilesSearchProvider
from acquiscout.sources.planner import GroqPlanner
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.tavily import TavilySearchProvider
from acquiscout.sources.usage import UsageTracker


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summarize(timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in timings:
        grouped[item["node"]].append(float(item["elapsed_ms"]))
    rows = []
    for node, values in grouped.items():
        rows.append(
            {
                "node": node,
                "calls": len(values),
                "total_ms": round(sum(values), 1),
                "mean_ms": round(sum(values) / len(values), 1),
                "max_ms": round(max(values), 1),
            }
        )
    rows.sort(key=lambda row: row["total_ms"], reverse=True)
    return rows


async def run(output_dir: Path) -> int:
    settings = get_settings()
    cache = SQLiteCache(
        settings.search_cache_db_path,
        settings.discovery_cache_ttl_hours * 3600,
    )
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

    graph = build_discovery_graph(deps, memory_checkpointer())
    state = initial_state(settings, run_id)
    config = {"configurable": {"thread_id": run_id}}
    starts: dict[str, dict[str, Any]] = {}
    timings: list[dict[str, Any]] = []
    started = time.perf_counter()

    try:
        async for event in graph.astream(state, config, stream_mode="debug"):
            if event.get("type") == "task":
                payload = event.get("payload") or {}
                starts[payload.get("id", "")] = {
                    "node": payload.get("name", "?"),
                    "step": event.get("step"),
                    "start": _timestamp(event["timestamp"]),
                }
            elif event.get("type") == "task_result":
                payload = event.get("payload") or {}
                start = starts.pop(payload.get("id", ""), None)
                if start is None:
                    continue
                elapsed = (_timestamp(event["timestamp"]) - start["start"]).total_seconds()
                timings.append(
                    {
                        "node": start["node"],
                        "step": start["step"],
                        "elapsed_ms": round(elapsed * 1000, 1),
                        "error": bool(payload.get("error")),
                    }
                )
        snapshot = await graph.aget_state(config)
        final = snapshot.values
        total_ms = round((time.perf_counter() - started) * 1000, 1)
        await archive.write_usage(
            {
                "totals": usage.totals(),
                "records": [record.model_dump(mode="json") for record in final.get("usage") or []],
            }
        )
        manifest = manifest_payload(final)
        manifest["raw_search_files"] = archive.search_files
        await archive.write_manifest(manifest)
    finally:
        await planner.aclose()
        await tavily.aclose()
        await govfiles.aclose()
        await directory.aclose()
        await registry_evidence.aclose()

    summary = _summarize(timings)
    payload = {
        "run_id": run_id,
        "enable_directory_resolver": settings.enable_directory_resolver,
        "total_ms": total_ms,
        "nodes": summary,
        "timings": timings,
        "final": {
            "run_status": final.get("run_status"),
            "plan": len(final.get("search_plan") or []),
            "candidates": len(final.get("deduplicated_candidates") or []),
            "companies": len(final.get("company_candidates") or []),
            "dropped": len(final.get("dropped_companies") or []),
            "usage": usage.totals(),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"latency_{run_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"run_id={run_id}")
    print(f"total_ms={total_ms}")
    print(f"artifact={path}")
    print(f"{'node':<28}{'calls':>6}{'total_ms':>12}{'mean_ms':>12}{'max_ms':>12}")
    for row in summary:
        print(
            f"{row['node']:<28}{row['calls']:>6}{row['total_ms']:>12.1f}"
            f"{row['mean_ms']:>12.1f}{row['max_ms']:>12.1f}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure discovery node latency")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/generated/latency"),
    )
    args = parser.parse_args()
    return asyncio.run(run(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
