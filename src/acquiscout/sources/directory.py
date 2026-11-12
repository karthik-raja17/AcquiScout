"""Experimental directory-page resolver: httpx + trafilatura + lxml, LLM fallback.

Not wired into the default discovery graph. Enable with
``enable_directory_resolver``; see ``docs/resolver-experiment.md``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from lxml import html as lxml_html  # type: ignore[import-untyped]

from acquiscout.config import Settings
from acquiscout.schemas import Candidate, Confidence, ResolvedEntity
from acquiscout.services.company_dedup import root_domain
from acquiscout.services.relevance import clean_company_name
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.policy import SourcePolicy

DIRECTORY_VERSION = "directory_v1"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


def _text(nodes: list[Any]) -> str:
    if not nodes:
        return ""
    node = nodes[0]
    return re.sub(r"\s+", " ", str(node)).strip()


def _json_nodes(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _json_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_nodes(item)


def _json_ld(html: lxml_html.HtmlElement) -> tuple[str | None, str | None]:
    name = url = None
    for script in html.xpath('//script[@type="application/ld+json"]'):
        raw = script.text_content().strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _json_nodes(data):
            types = node.get("@type")
            if isinstance(types, str):
                types = [types]
            if not isinstance(types, list):
                continue
            if not any(str(item).lower() in {"organization", "localbusiness"} for item in types):
                continue
            name = name or (str(node["name"]) if node.get("name") else None)
            url = url or (str(node["url"]) if node.get("url") else None)
    return name, url


def _meta(html: lxml_html.HtmlElement, key: str) -> str | None:
    values = html.xpath(f'//meta[@property="{key}"]/@content | //meta[@name="{key}"]/@content')
    return str(values[0]).strip() if values else None


def _canonical(html: lxml_html.HtmlElement) -> str | None:
    values = html.xpath('//link[@rel="canonical"]/@href')
    return str(values[0]).strip() if values else None


def _off_host_links(html: lxml_html.HtmlElement, base_url: str) -> list[str]:
    base_host = (urlsplit(base_url).hostname or "").lower().removeprefix("www.")
    links: list[str] = []
    for href in html.xpath("//a/@href"):
        candidate = urljoin(base_url, str(href))
        host = (urlsplit(candidate).hostname or "").lower().removeprefix("www.")
        if not host or host == base_host or "." not in host:
            continue
        if candidate not in links:
            links.append(candidate)
    return links


TITLE_FALLBACK_REJECT = re.compile(
    r"\b(best|jobs?|companies|directory|near|top|category|schools?|government|sheriff|"
    r"county of|property appraiser|tax collector)\b",
    re.IGNORECASE,
)


def _title_fallback(candidate: Candidate) -> ResolvedEntity | None:
    title = candidate.title or ""
    if not title or TITLE_FALLBACK_REJECT.search(title):
        return None
    name = clean_company_name(title, candidate.identifier)
    if not name or name == "Unknown company" or len(name.split()) < 2:
        return None
    return ResolvedEntity(
        company_name=name,
        official_url=None,
        root_domain=None,
        method="title",
        confidence=Confidence.LOW,
        sources=[candidate],
        review_flags=["title_fallback", "needs_official_url"],
    )


def _best_link(name: str, links: list[str]) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
    if len(normalized) < 5:
        return None
    for link in links:
        host = re.sub(r"[^a-z0-9]+", "", (urlsplit(link).hostname or "").lower())
        if normalized in host or host in normalized:
            return link
    return None


class DirectoryResolver:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        cache: SQLiteCache | None = None,
        policy: SourcePolicy | None = None,
        groq_http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._groq_http = groq_http
        self._owns_http = http is None
        self._owns_groq = groq_http is None
        self._cache = cache
        self._policy = policy or SourcePolicy()
        self.last_usage: dict[str, Any] | None = None

    def _cache_key(self, candidate: Candidate) -> tuple[str, str, dict[str, object], str]:
        return (
            "directory",
            candidate.identifier,
            {"kind": "resolved_entity"},
            DIRECTORY_VERSION,
        )

    async def _get_cached(self, candidate: Candidate) -> ResolvedEntity | None:
        if self._cache is None:
            return None
        payload = await asyncio.to_thread(self._cache.get, *self._cache_key(candidate))
        if isinstance(payload, dict):
            try:
                return ResolvedEntity.model_validate(payload)
            except Exception:
                return None
        return None

    async def _set_cached(self, candidate: Candidate, entity: ResolvedEntity) -> None:
        if self._cache is None:
            return
        await asyncio.to_thread(
            self._cache.set,
            *self._cache_key(candidate),
            entity.model_dump(mode="json"),
        )

    async def resolve(self, candidate: Candidate) -> ResolvedEntity | None:
        self.last_usage = None
        self._policy.enforce(candidate.identifier)
        cached = await self._get_cached(candidate)
        if cached is not None:
            return cached

        client = self._http or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers={"User-Agent": "AcquiScout/0.1 (+approved-source research)"},
        )
        own_http = self._http is None
        try:
            response = await client.get(candidate.identifier)
            response.raise_for_status()
        except Exception:
            if own_http:
                await client.aclose()
            return _title_fallback(candidate)

        try:
            html = lxml_html.fromstring(response.text)
        except Exception:
            if own_http:
                await client.aclose()
            return _title_fallback(candidate)

        title = _text(html.xpath("//title/text()"))
        og_title = _meta(html, "og:title")
        og_url = _meta(html, "og:url")
        canonical = _canonical(html)
        json_name, json_url = _json_ld(html)
        links = _off_host_links(html, candidate.identifier)
        raw_name = json_name or og_title or title or candidate.title
        name = clean_company_name(raw_name, candidate.identifier) if raw_name else ""

        method = "title"
        official_url = None
        if json_name:
            method = "json_ld"
            official_url = json_url
        elif og_title:
            method = "metadata"
            official_url = og_url
        directory_host = (
            (urlsplit(candidate.identifier).hostname or "").lower().removeprefix("www.")
        )
        if official_url is not None:
            host = (urlsplit(official_url).hostname or "").lower().removeprefix("www.")
            if host == directory_host:
                official_url = None
        if official_url is None and canonical:
            host = (urlsplit(canonical).hostname or "").lower().removeprefix("www.")
            if host != directory_host:
                official_url = canonical
        if official_url is None and name:
            official_url = _best_link(name, links)

        if name:
            entity = ResolvedEntity(
                company_name=name,
                official_url=official_url,
                root_domain=root_domain(official_url),
                method=method,  # type: ignore[arg-type]
                confidence=Confidence.MEDIUM if official_url else Confidence.LOW,
                sources=[candidate],
                review_flags=[] if official_url else ["needs_official_url"],
            )
            await self._set_cached(candidate, entity)
            if own_http:
                await client.aclose()
            return entity

        llm_entity = await self._llm(candidate, title or og_title, response.text, links)
        if llm_entity is not None:
            await self._set_cached(candidate, llm_entity)
            if own_http:
                await client.aclose()
            return llm_entity
        fallback = _title_fallback(candidate)
        if fallback is not None:
            await self._set_cached(candidate, fallback)
        if own_http:
            await client.aclose()
        return fallback

    async def _llm(
        self,
        candidate: Candidate,
        title: str | None,
        html: str,
        links: list[str],
    ) -> ResolvedEntity | None:
        if self._settings.groq_api_key is None:
            return None
        text = (
            trafilatura.extract(
                html, include_comments=False, include_tables=True, favor_precision=True
            )
            or ""
        )
        prompt = (
            "Extract the company this directory page is about. Return JSON only with "
            'keys "company_name", "official_url", "confidence". If no official URL is '
            "present, use null. Do not invent a URL.\n\n"
            f"Directory URL: {candidate.identifier}\n"
            f"Page title: {title or ''}\n"
            f"Allowed URLs: {json.dumps(links[:80])}\n\n"
            f"Text:\n{text[:4000]}"
        )
        body = {
            "model": self._settings.groq_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.groq_api_key.get_secret_value()}"}
        client = self._groq_http or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        own_http = self._groq_http is None
        try:
            response = await client.post(GROQ_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
            content = json.loads(payload["choices"][0]["message"]["content"])
            usage = payload.get("usage")
            self.last_usage = usage if isinstance(usage, dict) else None
        except Exception:
            return None
        finally:
            if own_http:
                await client.aclose()

        name = str(content.get("company_name") or "").strip()
        if not name:
            return None
        url = content.get("official_url")
        flags: list[str] = []
        if url:
            url = str(url)
            if url not in links:
                flags.append("unverified_llm_url")
                url = None
        confidence = str(content.get("confidence", "low")).lower()
        level = (
            Confidence.HIGH
            if confidence == "high"
            else Confidence.MEDIUM
            if confidence == "medium"
            else Confidence.LOW
        )
        return ResolvedEntity(
            company_name=clean_company_name(name, candidate.identifier),
            official_url=url,
            root_domain=root_domain(url),
            method="llm",
            confidence=level,
            sources=[candidate],
            review_flags=flags or ([] if url else ["needs_official_url"]),
        )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
        if self._owns_groq and self._groq_http is not None:
            await self._groq_http.aclose()


__all__ = ["DIRECTORY_VERSION", "DirectoryResolver"]
