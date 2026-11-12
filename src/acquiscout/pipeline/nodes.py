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
from acquiscout.schemas import (
    Candidate,
    Confidence,
    QueryHit,
    ResolvedEntity,
    SearchTask,
    UsageRecord,
)
from acquiscout.services.company_dedup import dedup_entities, root_domain
from acquiscout.services.company_url import CompanyUrlResolver, high_confidence_company
from acquiscout.services.relevance import (
    RelevanceLabel,
    classify,
    clean_company_name,
)
from acquiscout.sources.archive import RunArchive
from acquiscout.sources.directory import DirectoryResolver
from acquiscout.sources.dlq import push_failure
from acquiscout.sources.planner import fallback_plan
from acquiscout.sources.policy import SourcePolicy
from acquiscout.sources.providers import Planner, RegistryResolver, SearchProvider
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.usage import BudgetExceeded, UsageTracker

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
    archive: RunArchive | None = None
    usage: UsageTracker | None = None
    directory: DirectoryResolver | None = None
    registry_evidence: RegistryEvidenceResolver | None = None
    company_url: CompanyUrlResolver | None = None


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
    return (
        "registry_id",
        candidate.provider,
        candidate.registry_jurisdiction or "",
        candidate.identifier,
    )


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
    public_entity_url = _http_url(result.get("url"))
    return Candidate(
        identifier=str(entity_id),
        identifier_kind="registry_id",
        registry_jurisdiction=(str(result["jurisdiction"]) if result.get("jurisdiction") else None),
        source_type="registry",
        provider=provider,
        title=result.get("legal_name"),
        snippet=None,
        public_entity_url=public_entity_url,
        resolution_status="none",
        found_by=[hit],
        first_seen_at=now,
    )


def manifest_payload(state: DiscoveryState) -> dict[str, Any]:
    return {
        "run_status": state.get("run_status"),
        "abort_reason": state.get("abort_reason"),
        "target_market": state.get("target_market"),
        "target_sector": state.get("target_sector"),
        "icp": state.get("icp"),
        "search_plan": [task.model_dump(mode="json") for task in state.get("search_plan") or []],
        "raw_result_count": len(state.get("raw_search_results") or []),
        "candidate_count": len(state.get("deduplicated_candidates") or []),
        "directory_candidate_count": len(state.get("directory_candidates") or []),
        "resolved_entity_count": len(state.get("resolved_entities") or []),
        "company_candidate_count": len(state.get("company_candidates") or []),
        "registry_profile_count": sum(
            1
            for company in state.get("company_candidates") or []
            if company.registry_profile is not None
        ),
        "public_registry_evidence_count": sum(
            1
            for company in state.get("company_candidates") or []
            if company.registry_profile is not None
            and company.registry_profile.evidence_status == "public_detail"
        ),
        "relevance_dropped": state.get("relevance_dropped") or [],
        "dropped_company_count": len(state.get("dropped_companies") or []),
        "search_failure_count": state.get("search_failure_count", 0),
        "provider_errors": state.get("provider_errors") or [],
        "archive_errors": state.get("archive_errors") or [],
        "usage": [record.model_dump(mode="json") for record in state.get("usage") or []],
        "captured_at": datetime.now(UTC).isoformat(),
    }


def _search_credits(provider: str, task: SearchTask) -> int:
    if provider == "tavily_web":
        return 2 if task.params.get("search_depth") == "advanced" else 1
    return 1


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
        error_entry: dict[str, Any] | None = None
        try:
            plan = await deps.planner.plan(state["icp"], SEARCH_HINTS)
            source = "planner"
        except Exception as exc:
            error_entry = _failure_entry("planner", "search_plan", state["run_id"], exc)
            await _push(error_entry)
            plan = fallback_plan(state["icp"], SEARCH_HINTS, settings.search_plan_size)
            source = "fallback"

        archive_errors: list[str] = []
        if deps.archive is not None:
            archived = await deps.archive.write_plan(
                {
                    "source": source,
                    "search_plan": [task.model_dump(mode="json") for task in plan],
                    "planner_raw": getattr(deps.planner, "last_raw", None),
                    "error": error_entry,
                }
            )
            if archived is None:
                archive_errors.append("plan archive failed")

        usage_records: list[UsageRecord] = []
        last_usage = getattr(deps.planner, "last_usage", None)
        if isinstance(last_usage, dict):
            record = UsageRecord(
                run_id=state["run_id"],
                provider="groq",
                operation="planner",
                prompt_tokens=int(last_usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(last_usage.get("completion_tokens", 0) or 0),
                total_tokens=int(last_usage.get("total_tokens", 0) or 0),
                detail={"model": settings.groq_model, "source": source},
            )
            usage_records.append(record)
            if deps.usage is not None:
                await deps.usage.record(record)

        result: dict[str, Any] = {
            "search_plan": plan,
            "usage": usage_records,
            "archive_errors": archive_errors,
        }
        if error_entry is not None:
            result["provider_errors"] = [_compact_failure(error_entry)]
        return result

    def make_search_node(
        provider: SearchProvider,
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def search_node(payload: dict[str, Any]) -> dict[str, Any]:
            task: SearchTask = payload["task"]
            run_id = str(payload.get("run_id", "unknown"))
            credits = _search_credits(provider.name, task)
            archive_errors: list[str] = []

            try:
                if deps.usage is not None:
                    if provider.name == "tavily_web":
                        await deps.usage.check_tavily(credits)
                    else:
                        await deps.usage.check_govfiles(credits)
            except BudgetExceeded as exc:
                entry = _failure_entry(provider.name, task.query, run_id, exc)
                await _push(entry)
                if deps.archive is not None:
                    archived = await deps.archive.write_search(
                        provider.name,
                        task.task_id,
                        {
                            "task": task.model_dump(mode="json"),
                            "results": [],
                            "raw": None,
                            "usage": None,
                            "error": entry,
                        },
                    )
                    if archived is None:
                        archive_errors.append(f"search archive failed: {task.task_id}")
                return {
                    "raw_search_results": [],
                    "search_failure_count": 1,
                    "provider_errors": [_compact_failure(entry)],
                    "usage": [],
                    "archive_errors": archive_errors,
                }

            try:
                search_raw = getattr(provider, "search_raw", None)
                if callable(search_raw):
                    results, raw = await search_raw(task)
                else:
                    results = await provider.search(task)
                    raw = None
            except Exception as exc:
                entry = _failure_entry(provider.name, task.query, run_id, exc)
                await _push(entry)
                if deps.archive is not None:
                    archived = await deps.archive.write_search(
                        provider.name,
                        task.task_id,
                        {
                            "task": task.model_dump(mode="json"),
                            "results": [],
                            "raw": None,
                            "usage": None,
                            "error": entry,
                        },
                    )
                    if archived is None:
                        archive_errors.append(f"search archive failed: {task.task_id}")
                return {
                    "raw_search_results": [],
                    "search_failure_count": 1,
                    "provider_errors": [_compact_failure(entry)],
                    "usage": [],
                    "archive_errors": archive_errors,
                }

            now = datetime.now(UTC)
            candidates = [
                candidate
                for result in results
                if (candidate := _to_candidate(result, provider.name, task, now)) is not None
            ]
            usage_record = UsageRecord(
                run_id=run_id,
                provider=provider.name,
                operation="search",
                credits=credits,
                detail={"query": task.query, "node": task.node},
            )
            if deps.usage is not None:
                await deps.usage.record(usage_record)

            if deps.archive is not None:
                archived = await deps.archive.write_search(
                    provider.name,
                    task.task_id,
                    {
                        "task": task.model_dump(mode="json"),
                        "results": results,
                        "raw": raw,
                        "usage": usage_record.model_dump(mode="json"),
                        "error": None,
                    },
                )
                if archived is None:
                    archive_errors.append(f"search archive failed: {task.task_id}")

            return {
                "raw_search_results": candidates,
                "search_failure_count": 0,
                "provider_errors": [],
                "usage": [usage_record],
                "archive_errors": archive_errors,
            }

        return search_node

    async def collect_node(state: DiscoveryState) -> dict[str, Any]:
        archive_errors: list[str] = []
        if deps.archive is not None:
            archived = await deps.archive.write_manifest(manifest_payload(state))
            if archived is None:
                archive_errors.append("manifest archive failed")
        return {"archive_errors": archive_errors}

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
        archive_errors: list[str] = []
        if deps.archive is not None:
            payload = manifest_payload(state)
            payload["run_status"] = "aborted"
            payload["abort_reason"] = reason
            archived = await deps.archive.write_manifest(payload)
            if archived is None:
                archive_errors.append("manifest archive failed")
        return {
            "raw_search_results": None,
            "deduplicated_candidates": [],
            "run_status": "aborted",
            "abort_reason": reason,
            "archive_errors": archive_errors,
        }

    async def relevance_node(state: DiscoveryState) -> dict[str, Any]:
        resolved: list[ResolvedEntity] = []
        directories: list[Candidate] = []
        dropped: list[dict[str, Any]] = []
        for candidate in state.get("deduplicated_candidates") or []:
            label = classify(candidate)
            if label in {RelevanceLabel.GOVERNMENT, RelevanceLabel.NEWS}:
                dropped.append(
                    {
                        "identifier": candidate.identifier,
                        "label": label.value,
                        "reason": f"relevance:{label.value}",
                    }
                )
                continue
            if label is RelevanceLabel.DIRECTORY:
                if settings.enable_directory_resolver:
                    directories.append(candidate)
                else:
                    dropped.append(
                        {
                            "identifier": candidate.identifier,
                            "label": "directory",
                            "reason": "directory_skipped",
                        }
                    )
                continue
            if label is RelevanceLabel.REGISTRY:
                registry_id = (
                    f"{candidate.registry_jurisdiction}:{candidate.identifier}"
                    if candidate.registry_jurisdiction
                    else candidate.identifier
                )
                resolved.append(
                    ResolvedEntity(
                        company_name=candidate.title or candidate.identifier,
                        official_url=None,
                        root_domain=None,
                        method="registry",
                        confidence=Confidence.MEDIUM,
                        registry_ids=[registry_id],
                        sources=[candidate],
                    )
                )
                continue
            official_url = candidate.crawl_url or candidate.identifier
            resolved.append(
                ResolvedEntity(
                    company_name=clean_company_name(candidate.title, candidate.identifier),
                    official_url=official_url,
                    root_domain=root_domain(official_url),
                    method="web",
                    confidence=Confidence.MEDIUM,
                    sources=[candidate],
                )
            )
        return {
            "resolved_entities": resolved,
            "directory_candidates": directories,
            "relevance_dropped": dropped,
        }

    async def resolve_directories_node(state: DiscoveryState) -> dict[str, Any]:
        entities: list[ResolvedEntity] = []
        dropped: list[dict[str, Any]] = []
        usage_records: list[UsageRecord] = []
        archive_errors: list[str] = []
        for candidate in state.get("directory_candidates") or []:
            entity = None
            error: str | None = None
            if deps.directory is None:
                error = "directory_resolver_unavailable"
            else:
                try:
                    entity = await deps.directory.resolve(candidate)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                last_usage = getattr(deps.directory, "last_usage", None)
                if isinstance(last_usage, dict):
                    record = UsageRecord(
                        run_id=state["run_id"],
                        provider="groq",
                        operation="directory_resolve",
                        prompt_tokens=int(last_usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(last_usage.get("completion_tokens", 0) or 0),
                        total_tokens=int(last_usage.get("total_tokens", 0) or 0),
                        detail={"url": candidate.identifier},
                    )
                    usage_records.append(record)
                    if deps.usage is not None:
                        await deps.usage.record(record)
            if entity is not None and entity.official_url:
                resolved_label = classify(
                    Candidate(
                        identifier=entity.official_url,
                        identifier_kind="url",
                        source_type="web",
                        provider="directory",
                        title=entity.company_name,
                    )
                )
                if resolved_label in {RelevanceLabel.GOVERNMENT, RelevanceLabel.NEWS}:
                    error = f"resolved:{resolved_label.value}"
                    entity = None
            if entity is None:
                dropped.append(
                    {
                        "identifier": candidate.identifier,
                        "label": "directory",
                        "reason": error or "directory_unresolved",
                    }
                )
            else:
                entities.append(entity)
            if deps.archive is not None:
                archived = await deps.archive.write_directory(
                    candidate.identifier,
                    {
                        "candidate": candidate.model_dump(mode="json"),
                        "entity": entity.model_dump(mode="json") if entity else None,
                        "error": error,
                    },
                )
                if archived is None:
                    archive_errors.append(f"directory archive failed: {candidate.identifier}")
        return {
            "resolved_entities": entities,
            "relevance_dropped": dropped,
            "usage": usage_records,
            "archive_errors": archive_errors,
        }

    async def enrich_registry_node(state: DiscoveryState) -> dict[str, Any]:
        entities = list(state.get("resolved_entities") or [])
        enriched: list[ResolvedEntity] = []
        usage_records: list[UsageRecord] = []
        archive_errors: list[str] = []
        get_company = getattr(deps.govfiles, "get_company", None)

        for entity in entities:
            if not entity.registry_ids:
                enriched.append(entity)
                continue
            registry_id = entity.registry_ids[0]
            jurisdiction, separator, number = registry_id.partition(":")
            if not separator or not callable(get_company):
                enriched.append(entity)
                continue
            try:
                if deps.usage is not None:
                    await deps.usage.check_govfiles(1)
                profile = await get_company(jurisdiction, number)
            except Exception as exc:
                archive_errors.append(
                    f"registry enrichment failed for {registry_id}: {type(exc).__name__}"
                )
                enriched.append(entity)
                continue

            public_entity_url = next(
                (source.public_entity_url for source in entity.sources if source.public_entity_url),
                None,
            )
            if profile.public_entity_url is None and public_entity_url:
                profile = profile.model_copy(update={"public_entity_url": public_entity_url})
            enriched.append(entity.model_copy(update={"registry_profile": profile}))
            record = UsageRecord(
                run_id=state["run_id"],
                provider="govfiles_registry",
                operation="detail",
                credits=1,
                detail={"registry_id": registry_id},
            )
            usage_records.append(record)
            if deps.usage is not None:
                await deps.usage.record(record)
            if deps.archive is not None:
                archived = await deps.archive.write_registry(
                    registry_id,
                    {"profile": profile.model_dump(mode="json")},
                )
                if archived is None:
                    archive_errors.append(f"registry archive failed: {registry_id}")

        return {
            "enriched_entities": enriched,
            "usage": usage_records,
            "archive_errors": archive_errors,
        }

    async def resolve_registry_evidence_node(state: DiscoveryState) -> dict[str, Any]:
        entities = list(state.get("enriched_entities") or state.get("resolved_entities") or [])
        evidenced: list[ResolvedEntity] = []
        archive_errors: list[str] = []

        for entity in entities:
            profile = entity.registry_profile
            if profile is None:
                evidenced.append(entity)
                continue
            evidence = None
            if deps.registry_evidence is not None and profile.public_entity_url:
                try:
                    evidence = await deps.registry_evidence.resolve(
                        profile.public_entity_url,
                        entity.registry_ids[0].partition(":")[2] if entity.registry_ids else "",
                    )
                except Exception as exc:
                    archive_errors.append(
                        f"registry evidence failed for {entity.company_name}: {type(exc).__name__}"
                    )
            if evidence is not None:
                update: dict[str, Any] = {
                    "public_detail_url": evidence.public_detail_url,
                    "evidence_status": evidence.evidence_status,
                    "evidence_reason": evidence.reason,
                    "evidence_source": evidence.source,
                    "evidence_http_status": evidence.http_status,
                    "evidence_backend": evidence.backend,
                    "evidence_checked_at": evidence.fetched_at,
                }
                for field in (
                    "status",
                    "legal_form",
                    "formed_on",
                    "registered_address",
                ):
                    value = getattr(evidence, field)
                    if value is not None:
                        update[field] = value
                if evidence.officers:
                    update["officers"] = evidence.officers
                profile = profile.model_copy(update=update)
            else:
                profile = profile.model_copy(
                    update={
                        "evidence_status": "failed",
                        "evidence_reason": "resolver_unavailable",
                    }
                )
            evidenced.append(entity.model_copy(update={"registry_profile": profile}))
            if deps.archive is not None:
                last_text = (
                    getattr(deps.registry_evidence, "last_text", "")
                    if deps.registry_evidence is not None
                    else ""
                )
                archived = await deps.archive.write_registry_evidence(
                    entity.registry_ids[0] if entity.registry_ids else entity.company_name,
                    {
                        "profile": profile.model_dump(mode="json"),
                        "source_url": profile.public_entity_url,
                        "evidence_status": profile.evidence_status,
                        "evidence_reason": profile.evidence_reason,
                        "evidence_source": profile.evidence_source,
                        "evidence_http_status": profile.evidence_http_status,
                        "evidence_backend": profile.evidence_backend,
                        "last_text_chars": evidence.text_chars
                        if evidence
                        else len(last_text or ""),
                    },
                )
                if archived is None:
                    archive_errors.append(
                        f"registry evidence archive failed: {entity.company_name}"
                    )

        return {"evidenced_entities": evidenced, "archive_errors": archive_errors}

    async def company_dedup_node(state: DiscoveryState) -> dict[str, Any]:
        entities = (
            state.get("evidenced_entities")
            or state.get("enriched_entities")
            or state.get("resolved_entities")
            or []
        )
        return {"company_candidates": dedup_entities(entities)}

    async def resolve_company_urls_node(state: DiscoveryState) -> dict[str, Any]:
        companies = list(state.get("company_candidates") or [])
        updated = []
        usage_records: list[UsageRecord] = []
        archive_errors: list[str] = []
        for company in companies:
            if (
                company.official_url is not None
                or deps.company_url is None
                or not high_confidence_company(company, allow_directory=True)
            ):
                updated.append(company)
                continue
            result = None
            error: str | None = None
            try:
                if deps.usage is not None:
                    await deps.usage.check_tavily(1)
                result = await deps.company_url.resolve(company.company_name)
                record = UsageRecord(
                    run_id=state["run_id"],
                    provider="tavily_web",
                    operation="company_url_search",
                    credits=1,
                    detail={"company_key": company.company_key},
                )
                usage_records.append(record)
                if deps.usage is not None:
                    await deps.usage.record(record)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            if result is not None and result.official_url:
                updated.append(
                    company.model_copy(
                        update={
                            "official_url": result.official_url,
                            "root_domain": root_domain(result.official_url),
                            "official_url_method": "search",
                            "confidence": result.confidence,
                            "review_flags": list(
                                dict.fromkeys([*company.review_flags, *result.review_flags])
                            ),
                        }
                    )
                )
            else:
                flags = list(company.review_flags)
                if result is not None:
                    flags.extend(result.review_flags)
                elif error:
                    flags.append("url_search_failed")
                updated.append(
                    company.model_copy(update={"review_flags": list(dict.fromkeys(flags))})
                )
            if deps.archive is not None:
                archived = await deps.archive.write_company_url(
                    company.company_key,
                    {
                        "company_name": company.company_name,
                        "result": {
                            "official_url": result.official_url if result else None,
                            "confidence": result.confidence.value if result else None,
                            "review_flags": result.review_flags if result else [],
                        },
                        "error": error,
                    },
                )
                if archived is None:
                    archive_errors.append(f"company url archive failed: {company.company_key}")
        return {
            "company_candidates": updated,
            "usage": usage_records,
            "archive_errors": archive_errors,
        }

    async def screen_companies_node(state: DiscoveryState) -> dict[str, Any]:
        kept = []
        dropped: list[dict[str, Any]] = []
        for company in state.get("company_candidates") or []:
            profile = company.registry_profile
            if company.official_url is not None:
                kept.append(company)
                continue
            if profile is not None and profile.evidence_status == "public_detail":
                kept.append(company)
                continue
            if profile is None:
                reason = "missing_official_url"
            elif profile.evidence_status in {"blocked", "rate_limited"}:
                reason = "registry_evidence_blocked"
            elif profile.evidence_status in {
                "http_error",
                "network_error",
                "low_quality",
                "unsupported_source",
                "parse_failed",
                "failed",
            }:
                reason = "registry_evidence_failed"
            else:
                reason = "missing_public_registry_evidence"
            dropped.append(
                {
                    "company_key": company.company_key,
                    "company_name": company.company_name,
                    "registry_ids": company.registry_ids,
                    "evidence_status": profile.evidence_status if profile else None,
                    "evidence_reason": profile.evidence_reason if profile else None,
                    "evidence_source": profile.evidence_source if profile else None,
                    "evidence_http_status": profile.evidence_http_status if profile else None,
                    "public_entity_url": profile.public_entity_url if profile else None,
                    "public_detail_url": profile.public_detail_url if profile else None,
                    "reason": reason,
                }
            )
        if deps.archive is not None:
            await deps.archive.write_dropped({"dropped": dropped})
        return {"company_candidates": kept, "dropped_companies": dropped}

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
            resolved.append(candidate.model_copy(update={"resolution_status": "none"}))
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
        "relevance": relevance_node,
        "resolve_directories": resolve_directories_node,
        "company_dedup": company_dedup_node,
        "enrich_registry": enrich_registry_node,
        "resolve_registry_evidence": resolve_registry_evidence_node,
        "resolve_company_urls": resolve_company_urls_node,
        "screen_companies": screen_companies_node,
    }


__all__ = [
    "DiscoveryDeps",
    "canonicalize_url",
    "make_nodes",
    "path_priority",
    "rank_key",
]
