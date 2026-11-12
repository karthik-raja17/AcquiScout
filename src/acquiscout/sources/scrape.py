"""Jina-first, Firecrawl-fallback scraping with policy, retries, and cache-aside.

Jina AI Reader is free and carries the dev workload. Firecrawl spends one-time
credits, so it is only used when Jina returns low-quality text AND the fallback
is explicitly enabled in settings.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from acquiscout.config import Settings
from acquiscout.sources.circuit import HostCircuitBreaker
from acquiscout.sources.policy import SourcePolicy
from acquiscout.sources.ratelimit import AsyncRateLimiter

EXTRACTOR_VERSION = "v1"
JINA_ENDPOINT = "https://r.jina.ai/"
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"

ScrapeStatus = Literal[
    "ok",
    "blocked",
    "rate_limited",
    "http_error",
    "network_error",
    "low_quality",
    "not_found",
]

BLOCK_MARKERS = (
    "just a moment",
    "enable javascript",
    "cf-chl",
    "attention required",
    "access denied",
    "captcha",
    "are you a robot",
    "target url returned error 403",
)

TARGET_STATUS = re.compile(r"Target URL returned error (\d{3})", re.IGNORECASE)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TITLE_LINE = re.compile(r"^Title:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


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
    status: ScrapeStatus = "ok"
    status_code: int | None = None
    target_status: int | None = None
    title: str | None = None
    retry_after_seconds: float | None = None
    circuit_failure: bool = False


@dataclass(frozen=True)
class _Attempt:
    text: str = ""
    status: ScrapeStatus = "ok"
    reason: str | None = None
    status_code: int | None = None
    target_status: int | None = None
    title: str | None = None
    retry_after_seconds: float | None = None
    circuit_failure: bool = False


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


def _title(text: str) -> str | None:
    match = TITLE_TAG.search(text) or TITLE_LINE.search(text)
    if match is None:
        return None
    return " ".join(match.group(1).split())[:200] or None


def _target_status(text: str) -> int | None:
    match = TARGET_STATUS.search(text)
    return int(match.group(1)) if match else None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _classify(
    text: str,
    status_code: int | None,
    *,
    retry_after_seconds: float | None = None,
    min_chars: int = 400,
) -> _Attempt:
    lowered = text.lower()
    target = _target_status(text)
    title = _title(text)
    blocked_marker = any(marker in lowered for marker in BLOCK_MARKERS)
    cloudflare = (
        any(
            marker in lowered
            for marker in ("just a moment", "enable javascript", "cf-chl", "attention required")
        )
        or target == 403
    )

    if status_code in (401, 403) or target == 403 or blocked_marker:
        reason = "cloudflare_challenge" if cloudflare else f"http_{status_code or 403}"
        return _Attempt(
            text=text,
            status="blocked",
            reason=reason,
            status_code=status_code,
            target_status=target or (403 if status_code == 403 else None),
            title=title,
            retry_after_seconds=retry_after_seconds,
            circuit_failure=True,
        )
    if status_code == 429:
        return _Attempt(
            text=text,
            status="rate_limited",
            reason="http_429",
            status_code=status_code,
            target_status=target,
            title=title,
            retry_after_seconds=retry_after_seconds,
            circuit_failure=True,
        )
    if status_code == 404:
        return _Attempt(
            text=text,
            status="not_found",
            reason="http_404",
            status_code=status_code,
            target_status=target,
            title=title,
        )
    if status_code is not None and status_code >= 500:
        return _Attempt(
            text=text,
            status="http_error",
            reason=f"http_{status_code}",
            status_code=status_code,
            target_status=target,
            title=title,
            retry_after_seconds=retry_after_seconds,
            circuit_failure=True,
        )
    if status_code is not None and status_code >= 400:
        return _Attempt(
            text=text,
            status="http_error",
            reason=f"http_{status_code}",
            status_code=status_code,
            target_status=target,
            title=title,
            retry_after_seconds=retry_after_seconds,
        )
    if not _quality_ok(text, min_chars):
        return _Attempt(
            text=text,
            status="low_quality",
            reason="low_quality",
            status_code=status_code,
            target_status=target,
            title=title,
        )
    return _Attempt(
        text=text,
        status="ok",
        status_code=status_code,
        target_status=target,
        title=title,
    )


def _error_attempt(prefix: str, exc: Exception) -> _Attempt:
    message = f"{prefix}: {exc}"
    if "upstream 5" in message:
        return _Attempt(status="http_error", reason=message, circuit_failure=True)
    return _Attempt(status="network_error", reason=message, circuit_failure=True)


def _result(
    url: str, attempt: _Attempt, backend: str, *, reason: str | None = None
) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        ok=attempt.status == "ok",
        text=attempt.text,
        backend=backend,
        reason=reason if reason is not None else attempt.reason,
        status=attempt.status,
        status_code=attempt.status_code,
        target_status=attempt.target_status,
        title=attempt.title,
        retry_after_seconds=attempt.retry_after_seconds,
        circuit_failure=attempt.circuit_failure,
    )


class Scraper:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        cache: Cache | None = None,
        policy: SourcePolicy | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
        circuit_breaker: HostCircuitBreaker | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._owns_http = http is None
        self._cache = cache if cache is not None else RedisCache(settings.redis_url)
        self._policy = policy or SourcePolicy()
        self._rate_limiter = rate_limiter or AsyncRateLimiter(
            settings.jina_requests_per_minute, 60.0
        )
        self._circuit = circuit_breaker
        if self._circuit is None and settings.scrape_circuit_enabled:
            self._circuit = HostCircuitBreaker(
                failure_threshold=settings.scrape_circuit_failure_threshold,
                cooldown_seconds=settings.scrape_circuit_cooldown_seconds,
            )

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

        if self._circuit is not None:
            decision = await self._circuit.before(url)
            if not decision.allowed:
                return ScrapeResult(
                    url=url,
                    ok=False,
                    backend="circuit",
                    status="blocked",
                    reason=decision.reason,
                    retry_after_seconds=decision.retry_after_seconds,
                )

        attempt = await self._jina(url)
        backend = "jina"
        if attempt.status != "ok" and self._settings.scrape_fallback_to_firecrawl:
            fallback = await self._firecrawl(url)
            if fallback.status == "ok" or attempt.status == "low_quality":
                attempt = fallback
                backend = "firecrawl"

        reason = attempt.reason
        if attempt.status == "low_quality":
            reason = f"{backend}_low_quality"
        result = _result(url, attempt, backend, reason=reason)

        if self._circuit is not None:
            await self._circuit.record(
                url,
                failed=result.circuit_failure,
                retry_after_seconds=result.retry_after_seconds,
            )

        if result.ok:
            await self._cache.set(
                self.cache_key(url),
                json.dumps({"text": result.text, "backend": backend}),
                self._settings.discovery_cache_ttl_hours * 3600,
            )
        return result

    async def _request(
        self, method: str, url: str, *, use_jina_limiter: bool = False, **kwargs: object
    ) -> httpx.Response:
        client = self._http_client()
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, ScrapeError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        ):
            with attempt:
                if use_jina_limiter:
                    await self._rate_limiter.acquire()
                response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        try:
                            await asyncio.sleep(min(float(retry_after), 60.0))
                        except ValueError:
                            pass
                    return response
                if response.status_code >= 500:
                    raise ScrapeError(f"upstream {response.status_code}")
                return response
        raise ScrapeError("retries exhausted")

    async def _jina(self, url: str) -> _Attempt:
        headers = {"Accept": "text/plain"}
        jina_key = self._settings.jina_api_key
        if jina_key is not None and jina_key.get_secret_value().strip():
            headers["Authorization"] = f"Bearer {jina_key.get_secret_value()}"
        try:
            response = await self._request(
                "GET", f"{JINA_ENDPOINT}{url}", use_jina_limiter=True, headers=headers
            )
        except (httpx.HTTPError, ScrapeError) as exc:
            return _error_attempt("jina_error", exc)
        return _classify(
            response.text,
            response.status_code,
            retry_after_seconds=_retry_after(response),
            min_chars=self._settings.jina_min_chars,
        )

    async def _firecrawl(self, url: str) -> _Attempt:
        firecrawl_key = self._settings.firecrawl_api_key
        if firecrawl_key is None or not firecrawl_key.get_secret_value().strip():
            return _Attempt(
                status="http_error",
                reason="firecrawl_key_missing",
            )
        token = firecrawl_key.get_secret_value()
        try:
            response = await self._request(
                "POST",
                FIRECRAWL_ENDPOINT,
                headers={"Authorization": f"Bearer {token}"},
                json={"url": url, "formats": ["markdown"]},
            )
        except (httpx.HTTPError, ScrapeError) as exc:
            return _error_attempt("firecrawl_error", exc)
        if response.status_code >= 400:
            return _classify(
                response.text,
                response.status_code,
                retry_after_seconds=_retry_after(response),
                min_chars=self._settings.jina_min_chars,
            )
        payload = response.json()
        if not payload.get("success", True):
            return _Attempt(status="low_quality", reason="firecrawl_unsuccessful")
        markdown = payload.get("data", {}).get("markdown", "")
        return _classify(
            markdown,
            200,
            min_chars=self._settings.jina_min_chars,
        )


__all__ = ["Cache", "EXTRACTOR_VERSION", "RedisCache", "ScrapeResult", "Scraper"]
