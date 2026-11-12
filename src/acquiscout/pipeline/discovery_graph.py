"""Discovery subgraph wiring and checkpointer setup."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from acquiscout.config import Settings
from acquiscout.observability import configure_langsmith, tracing_metadata
from acquiscout.pipeline.nodes import DiscoveryDeps, make_nodes, manifest_payload
from acquiscout.pipeline.state import DiscoveryState, initial_state

SERIALIZED_MODELS = [
    ("acquiscout.schemas", "Candidate"),
    ("acquiscout.schemas", "QueryHit"),
    ("acquiscout.schemas", "SearchTask"),
    ("acquiscout.schemas", "UsageRecord"),
    ("acquiscout.schemas", "ResolvedEntity"),
    ("acquiscout.schemas", "CompanyCandidate"),
    ("acquiscout.schemas", "RegistryOfficer"),
    ("acquiscout.schemas", "RegistryProfile"),
    ("acquiscout.schemas", "Confidence"),
]


def _serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=SERIALIZED_MODELS)


async def sqlite_checkpointer(
    settings: Settings,
) -> tuple[AsyncSqliteSaver, aiosqlite.Connection]:
    path = Path(settings.checkpoint_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.commit()
    return AsyncSqliteSaver(conn, serde=_serde()), conn


def sync_sqlite_checkpointer(
    settings: Settings,
) -> tuple[SqliteSaver, sqlite3.Connection]:
    """Read-only helper for the optional checkpoint fixture rescue."""
    path = Path(settings.checkpoint_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return SqliteSaver(conn, serde=_serde()), conn


def memory_checkpointer() -> MemorySaver:
    return MemorySaver(serde=_serde())


def fan_out(state: DiscoveryState) -> list[Send] | str:
    plan = state.get("search_plan") or []
    if not plan:
        return "abort_node"
    return [Send(task.node, {"task": task, "run_id": state["run_id"]}) for task in plan]


def build_discovery_graph(
    deps: DiscoveryDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> Any:
    nodes = make_nodes(deps)
    builder = StateGraph(DiscoveryState)
    builder.add_node("planner", nodes["planner"])
    builder.add_node("tavily_web", nodes["tavily_web"])
    builder.add_node("govfiles_registry", nodes["govfiles_registry"])
    builder.add_node("collect", nodes["collect"])
    builder.add_node("bouncer", nodes["bouncer"])
    builder.add_node("abort_node", nodes["abort_node"])
    builder.add_node("resolve", nodes["resolve"])
    builder.add_node("relevance", nodes["relevance"])
    builder.add_node("enrich_registry", nodes["enrich_registry"])
    builder.add_node("resolve_registry_evidence", nodes["resolve_registry_evidence"])
    builder.add_node("company_dedup", nodes["company_dedup"])
    builder.add_node("screen_companies", nodes["screen_companies"])

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner",
        fan_out,
        ["tavily_web", "govfiles_registry", "abort_node"],
    )
    builder.add_edge("tavily_web", "collect")
    builder.add_edge("govfiles_registry", "collect")
    builder.add_conditional_edges(
        "collect",
        nodes["route_after_search"],
        {"bouncer": "bouncer", "abort_node": "abort_node"},
    )
    builder.add_edge("bouncer", "resolve")
    builder.add_edge("resolve", "relevance")

    if deps.settings.enable_directory_resolver:
        builder.add_node("resolve_directories", nodes["resolve_directories"])
        builder.add_node("resolve_company_urls", nodes["resolve_company_urls"])
        builder.add_edge("relevance", "resolve_directories")
        builder.add_edge("resolve_directories", "enrich_registry")
        builder.add_edge("company_dedup", "resolve_company_urls")
        builder.add_edge("resolve_company_urls", "screen_companies")
    else:
        builder.add_edge("relevance", "enrich_registry")
        builder.add_edge("company_dedup", "screen_companies")

    builder.add_edge("enrich_registry", "resolve_registry_evidence")
    builder.add_edge("resolve_registry_evidence", "company_dedup")
    builder.add_edge("screen_companies", END)
    builder.add_edge("abort_node", END)
    return builder.compile(checkpointer=checkpointer)


async def run_discovery(
    deps: DiscoveryDeps,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    run_id: str | None = None,
) -> DiscoveryState:
    configure_langsmith(deps.settings)
    graph = build_discovery_graph(deps, checkpointer)
    state = initial_state(deps.settings, run_id)
    config = {
        "configurable": {"thread_id": state["run_id"]},
        "run_name": f"discovery-{state['run_id']}",
        "metadata": tracing_metadata(deps.settings),
    }
    final = cast(DiscoveryState, await graph.ainvoke(state, config))
    if deps.archive is not None:
        await deps.archive.write_usage(
            {
                "totals": deps.usage.totals() if deps.usage is not None else {},
                "records": [record.model_dump(mode="json") for record in final.get("usage") or []],
            }
        )
        manifest = manifest_payload(final)
        manifest["raw_search_files"] = deps.archive.search_files
        await deps.archive.write_manifest(manifest)
    return final


__all__ = [
    "SERIALIZED_MODELS",
    "build_discovery_graph",
    "fan_out",
    "memory_checkpointer",
    "run_discovery",
    "sqlite_checkpointer",
    "sync_sqlite_checkpointer",
]
