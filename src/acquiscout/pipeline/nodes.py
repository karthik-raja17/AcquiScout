"""Discovery nodes: plan, search, join, route, bouncer, abort, resolve."""

from __future__ import annotations

import re
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from acquiscout.config import Settings
from acquiscout.pipeline.icp import SEARCH_HINTS
from acquiscout.pipeline.state import DiscoveryState
from acquiscout.schemas import Candidate, QueryHit, SearchTask
from acquiscout.sources.dlq import push_failure
from acquiscout.sources.planner import fallback_plan
from acquiscout.sources.policy import SourcePolicy
from acquiscout.sources.providers import Planner, RegistryResolver, SearchProvider

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
}

PATH_PRIORITY = (
    (re.compile(r"^/?$"), 0),
    (re.compile(r"^/(about|about-us|company|our-story|history)/?$", re.I), 1),
    (re.compile(r"^/(team|our-team|leadership|management|people|staff)/?$", re.I), 2),
    (re.compile(r"^/(contact|contact-us)/?$", re.I), 3),
    (re.compile(r"^/(services|what-we-do|practice-areas)/?$", re.I), 4),
)
DEFAULT_PRIORITY = 99

_HTTP_URL = TypeAdapter(HttpUrl)


@dataclass
class DiscoveryDeps:
    settings: Settings
    planner: Planner
    tavily: SearchProvider
    govfiles: SearchProvider
    resolver: RegistryResolver | None = None
    policy: SourcePolicy = field(default_factory=SourcePolicy)
    dlq_push: Callable[[dict[str, Any]], Awaitable[bool]] | None = None
    redis_client: Any | None = None


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    query.sort()
    netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def path_priority(url: str) -> int:
    path = urlsplit(url).path
    for pattern, priority in PATH_PRIORITY:
        if pattern.search(path):
            return priority
    return DEFAULT_PRIORITY


def rank_key(candidate: Candidate) -> tuple[Any, ...]:
    hits = candidate.found_by
    return (
        path_priority(candidate.identifier)
        if candidate.identifier_kind == "url"
        else DEFAULT_PRIORITY,
        -len(hits),
        min((hit.search_plan_index for hit in hits), default=0),
        candidate.identifier,
    )


def _dedup_key(candidate: Candidate) -> tuple[str, ...]:
    if candidate.identifier_kind == "url":
        return ("url", canonicalize_url(candidate.identifier))
    return ("registry_id", candidate.provider, candidate.identifier)


def _merge(existing: Candidate, incoming: Candidate) -> Candidate:
    seen = {(hit.query, hit.provider, hit.search_plan_index) for hit in existing.found_by}
    hits = list(existing.found_by)
    for hit in incoming.found_by:
        key = (hit.query, hit.provider, hit.search_plan_index)
        if key not in seen:
            seen.add(key)
            hits.append(hit)
    flags = list(dict.fromkeys([*existing.review_flags, *incoming.review_flags]))
    return existing.model_copy(
        update={
            "found_by": hits,
            "title": existing.title or incoming.title,
            "snippet": existing.snippet or incoming.snippet,
            "crawl_url": existing.crawl_url or incoming.crawl_url,
            "review_flags": flags,
            "first_seen_at": min(existing.first_seen_at, incoming.first_seen_at),
        }
    )


def _http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return str(_HTTP_URL.validate_python(value))
    except Exception:
        return None


def _failure_entry(provider: str, query: str, run_id: str, exc: BaseException) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "provider": provider,
        "query": query,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "attempts": 3,
        "created_at": datetime.now(UTC).isoformat(),
        "traceback": traceback.format_exc(),
        "thread_id": run_id,
    }


def _compact_failure(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": entry["provider"],
        "query": entry["query"],
        "error": entry["error"],
        "error_type": entry["error_type"],
        "created_at": entry["created_at"],
    }


def _to_candidate(
    result: dict[str, Any], provider: str, task: SearchTask, now: datetime
) -> Candidate | None:
    hit = QueryHit(
        query=task.query,
        search_plan_index=task.search_plan_index,
        provider=provider,
        found_at=now,
    )
    if provider == "tavily_web":
        url = result.get("url")
        if not isinstance(url, str) or not url:
            return None
        return Candidate(
            identifier=url,
            identifier_kind="url",
            source_type="web",
            provider=provider,
            title=result.get("title"),
            snippet=result.get("snippet"),
            found_by=[hit],
            first_seen_at=now,
        )

    entity_id = result.get("entity_id")
    if entity_id is None or str(entity_id).strip() == "":
        return None
    crawl_url = _http_url(result.get("url"))
    return Candidate(
        identifier=str(entity_id),
        identifier_kind="registry_id",
        source_type="registry",
        provider=provider,
        title=result.get("legal_name"),
        snippet=None,
        crawl_url=crawl_url,
        resolution_status="resolved" if crawl_url is not None else "none",
        found_by=[hit],
        first_seen_at=now,
    )


def make_nodes(deps: DiscoveryDeps) -> dict[str, Any]:
    settings = deps.settings

    async def _push(entry: dict[str, Any]) -> None:
        if deps.dlq_push is not None:
            await deps.dlq_push(entry)
            return
        await push_failure(
            entry,
            redis_client=deps.redis_client,
            fallback_path=settings.dlq_fallback_path,
        )

    async def planner_node(state: DiscoveryState) -> dict[str, Any]:
        try:
            plan = await deps.planner.plan(state["icp"], SEARCH_HINTS)
        except Exception as exc:
            entry = _failure_entry("planner", "search_plan", state["run_id"], exc)
            await _push(entry)
            plan = fallback_plan(state["icp"], SEARCH_HINTS, settings.search_plan_size)
            return {"search_plan": plan, "provider_errors": [_compact_failure(entry)]}
        return {"search_plan": plan}

    def make_search_node(
        provider: SearchProvider,
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def search_node(payload: dict[str, Any]) -> dict[str, Any]:
            task: SearchTask = payload["task"]
            try:
                results = await provider.search(task)
            except Exception as exc:
                entry = _failure_entry(
                    provider.name, task.query, payload.get("run_id", "unknown"), exc
                )
                await _push(entry)
                return {
                    "raw_search_results": [],
                    "search_failure_count": 1,
                    "provider_errors": [_compact_failure(entry)],
                }
            now = datetime.now(UTC)
            candidates = [
                candidate
                for result in results
                if (candidate := _to_candidate(result, provider.name, task, now)) is not None
            ]
            return {
                "raw_search_results": candidates,
                "search_failure_count": 0,
                "provider_errors": [],
            }

        return search_node

    async def collect_node(state: DiscoveryState) -> dict[str, Any]:
        return {}

    async def route_after_search(state: DiscoveryState) -> str:
        total = len(state.get("search_plan") or [])
        failures = int(state.get("search_failure_count") or 0)
        if total == 0:
            return "abort_node"
        if failures / total > settings.failure_abort_ratio:
            return "abort_node"
        return "bouncer"

    async def bouncer_node(state: DiscoveryState) -> dict[str, Any]:
        merged: dict[tuple[str, ...], Candidate] = {}
        for candidate in state.get("raw_search_results") or []:
            decision = deps.policy.classify_candidate(candidate)
            if not decision.allowed:
                continue
            if decision.reason == "unclassified_pdf":
                candidate = candidate.model_copy(
                    update={
                        "review_flags": list(
                            dict.fromkeys([*candidate.review_flags, "unclassified_pdf"])
                        )
                    }
                )
            key = _dedup_key(candidate)
            existing = merged.get(key)
            if existing is None:
                if candidate.identifier_kind == "url":
                    candidate = candidate.model_copy(
                        update={"identifier": canonicalize_url(candidate.identifier)}
                    )
                merged[key] = candidate.model_copy(deep=True)
            else:
                merged[key] = _merge(existing, candidate)

        ordered = sorted(merged.values(), key=rank_key)
        per_domain: dict[str, int] = {}
        kept: list[Candidate] = []
        for candidate in ordered:
            if candidate.identifier_kind == "url":
                host = (urlsplit(candidate.identifier).hostname or "").lower().removeprefix("www.")
                if per_domain.get(host, 0) >= settings.max_urls_per_domain:
                    continue
                per_domain[host] = per_domain.get(host, 0) + 1
            kept.append(candidate)

        return {
            "deduplicated_candidates": kept,
            "raw_search_results": None,
            "run_status": "completed",
        }

    async def abort_node(state: DiscoveryState) -> dict[str, Any]:
        total = len(state.get("search_plan") or [])
        failures = int(state.get("search_failure_count") or 0)
        if total == 0:
            reason = "Search plan was empty"
        else:
            reason = (
                f"Search failure rate {failures}/{total} exceeds "
                f"{settings.failure_abort_ratio:.0%} threshold"
            )
        return {
            "raw_search_results": None,
            "deduplicated_candidates": [],
            "run_status": "aborted",
            "abort_reason": reason,
        }

    async def resolve_node(state: DiscoveryState) -> dict[str, Any]:
        resolved: list[Candidate] = []
        for candidate in state.get("deduplicated_candidates") or []:
            if candidate.identifier_kind == "url":
                url = _http_url(candidate.identifier)
                resolved.append(
                    candidate.model_copy(
                        update={
                            "crawl_url": url,
                            "resolution_status": "resolved" if url else "failed",
                        }
                    )
                )
                continue
            raw_url = None
            if deps.resolver is not None:
                try:
                    raw_url = await deps.resolver.resolve(candidate)
                except Exception:
                    raw_url = None
            crawl_url = _http_url(raw_url)
            resolved.append(
                candidate.model_copy(
                    update={
                        "crawl_url": crawl_url,
                        "resolution_status": "resolved" if crawl_url else "failed",
                    }
                )
            )
        return {"deduplicated_candidates": resolved}

    return {
        "planner": planner_node,
        "tavily_web": make_search_node(deps.tavily),
        "govfiles_registry": make_search_node(deps.govfiles),
        "collect": collect_node,
        "route_after_search": route_after_search,
        "bouncer": bouncer_node,
        "abort_node": abort_node,
        "resolve": resolve_node,
    }


__all__ = [
    "DiscoveryDeps",
    "canonicalize_url",
    "make_nodes",
    "path_priority",
    "rank_key",
]
