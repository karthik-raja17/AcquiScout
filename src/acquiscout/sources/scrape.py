"""Jina-first, Firecrawl-fallback scraping with policy, retries, and cache-aside.

Jina AI Reader is free and carries the dev workload. Firecrawl spends one-time
credits, so it is only used when Jina returns low-quality text AND the fallback
is explicitly enabled in settings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from acquiscout.config import Settings
from acquiscout.sources.policy import SourcePolicy

EXTRACTOR_VERSION = "v1"
JINA_ENDPOINT = "https://r.jina.ai/"
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"

BLOCK_MARKERS = ("enable javascript", "access denied", "captcha", "are you a robot")


class ScrapeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScrapeResult:
    url: str
    ok: bool
    text: str = ""
    backend: str = ""
    cached: bool = False
    reason: str | None = None


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class RedisCache:
    """Lazy Redis cache-aside. Import failure degrades to no cache, never crashes."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: object | None = None

    async def _get_client(self) -> object:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def get(self, key: str) -> str | None:
        try:
            client = await self._get_client()
            return await client.get(key)  # type: ignore[attr-defined]
        except Exception:
            return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            client = await self._get_client()
            await client.set(key, value, ex=ttl_seconds)  # type: ignore[attr-defined]
        except Exception:
            return None


def _quality_ok(text: str, min_chars: int) -> bool:
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    lowered = stripped.lower()
    return not any(marker in lowered for marker in BLOCK_MARKERS)


class Scraper:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        cache: Cache | None = None,
        policy: SourcePolicy | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._owns_http = http is None
        self._cache = cache if cache is not None else RedisCache(settings.redis_url)
        self._policy = policy or SourcePolicy()

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"User-Agent": "AcquiScout/0.1 (+approved-source research)"},
            )
        return self._http

    def cache_key(self, url: str) -> str:
        return f"scrape:{EXTRACTOR_VERSION}:{url}"

    async def fetch(self, url: str) -> ScrapeResult:
        self._policy.enforce(url)

        cached = await self._cache.get(self.cache_key(url))
        if cached is not None:
            payload = json.loads(cached)
            return ScrapeResult(
                url=url, ok=True, text=payload["text"], backend=payload["backend"], cached=True
            )

        text, reason = await self._jina(url)
        backend = "jina"
        if not _quality_ok(text, self._settings.jina_min_chars):
            if not self._settings.scrape_fallback_to_firecrawl:
                return ScrapeResult(
                    url=url, ok=False, backend=backend, reason=reason or "jina_low_quality"
                )
            text, reason = await self._firecrawl(url)
            backend = "firecrawl"
            if not _quality_ok(text, self._settings.jina_min_chars):
                return ScrapeResult(
                    url=url, ok=False, backend=backend, reason=reason or "firecrawl_low_quality"
                )

        await self._cache.set(
            self.cache_key(url),
            json.dumps({"text": text, "backend": backend}),
            self._settings.discovery_cache_ttl_hours * 3600,
        )
        return ScrapeResult(url=url, ok=True, text=text, backend=backend)

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        client = self._http_client()
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, ScrapeError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
                if response.status_code == 429 or response.status_code >= 500:
                    raise ScrapeError(f"upstream {response.status_code}")
                return response
        raise ScrapeError("retries exhausted")

    async def _jina(self, url: str) -> tuple[str, str | None]:
        headers = {"Accept": "text/plain"}
        if self._settings.jina_api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.jina_api_key.get_secret_value()}"
        try:
            response = await self._request("GET", f"{JINA_ENDPOINT}{url}", headers=headers)
        except (httpx.HTTPError, ScrapeError) as exc:
            return "", f"jina_error: {exc}"
        if response.status_code >= 400:
            return "", f"jina_status: {response.status_code}"
        return response.text, None

    async def _firecrawl(self, url: str) -> tuple[str, str | None]:
        if self._settings.firecrawl_api_key is None:
            return "", "firecrawl_key_missing"
        token = self._settings.firecrawl_api_key.get_secret_value()
        try:
            response = await self._request(
                "POST",
                FIRECRAWL_ENDPOINT,
                headers={"Authorization": f"Bearer {token}"},
                json={"url": url, "formats": ["markdown"]},
            )
        except (httpx.HTTPError, ScrapeError) as exc:
            return "", f"firecrawl_error: {exc}"
        if response.status_code >= 400:
            return "", f"firecrawl_status: {response.status_code}"
        payload = response.json()
        if not payload.get("success", True):
            return "", "firecrawl_unsuccessful"
        return payload.get("data", {}).get("markdown", ""), None


__all__ = ["Cache", "EXTRACTOR_VERSION", "RedisCache", "ScrapeResult", "Scraper"]
