"""GovFiles Corporate Entity API adapter (v2).

Confirmed contract (2026-11-10):
- base URL: https://api.govfiles.dev
- auth: X-API-Key header
- search: POST /v2/companies/search with {"q": ..., "jurisdictions": "us_fl", ...}
- detail: GET /v2/companies/{jurisdiction_code}/{entity_number}
- detail exposes `entity_url` for crawling.
"""

from __future__ import annotations

import re
from typing import Any, cast

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from acquiscout.config import Settings
from acquiscout.schemas import Candidate, SearchTask
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.fixtures import (
    FixtureMissing,
    fixture_key,
    load_fixture,
    save_fixture,
)
from acquiscout.sources.providers import ProviderError

GOVFILES_VERSION = "govfiles_v3"
DEFAULT_BASE_URL = "https://api.govfiles.dev"
QUERY_NOISE = (
    "sunbiz",
    "florida division of corporations",
    "florida department of state",
    "registered agent",
)


class GovFilesSearchProvider:
    name = "govfiles_registry"

    def __init__(
        self,
        settings: Settings,
        cache: SQLiteCache | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._http = http
        self._owns_http = http is None

    def _base_url(self) -> str:
        return (self._settings.govfiles_base_url or DEFAULT_BASE_URL).rstrip("/")

    def _clean_query(self, query: str) -> str:
        cleaned = query
        for term in QUERY_NOISE:
            cleaned = re.sub(re.escape(term), " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or query

    def _params(self, task: SearchTask) -> dict[str, object]:
        params: dict[str, object] = {
            "limit": self._settings.search_max_results,
            "jurisdictions": "us_fl",
            "order_by": "relevance",
        }
        for key, value in task.params.items():
            if key == "max_results":
                params["limit"] = value
            else:
                params[key] = value
        return params

    async def search(self, task: SearchTask) -> list[dict[str, Any]]:
        params = self._params(task)
        query = self._clean_query(task.query)
        key = fixture_key(self.name, query, params, GOVFILES_VERSION)

        if self._settings.use_fixtures:
            payload = load_fixture(self._settings.dev_fixtures_dir, "search", self.name, key)
            return cast(list[dict[str, Any]], payload.get("results", []))

        if self._cache is not None:
            cached = self._cache.get(self.name, query, params, GOVFILES_VERSION)
            if isinstance(cached, dict):
                return cast(list[dict[str, Any]], cached.get("results", []))

        results = await self._live(query, params)
        if self._cache is not None:
            self._cache.set(self.name, query, params, GOVFILES_VERSION, {"results": results})
        if self._settings.capture_search:
            save_fixture(
                self._settings.dev_fixtures_dir,
                "search",
                self.name,
                key,
                {"results": results},
            )
        return results

    async def _live(self, query: str, params: dict[str, object]) -> list[dict[str, Any]]:
        body = {"q": query, **params}
        payload = await self._post(f"{self._base_url()}/v2/companies/search", body)
        raw_results = payload.get("results", [])
        normalized: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            nested = item.get("company")
            company = cast(dict[str, Any], nested if isinstance(nested, dict) else item)
            entity_id = company.get("entity_number") or company.get("entity_id")
            if not entity_id:
                continue
            normalized.append(
                {
                    "entity_id": str(entity_id),
                    "jurisdiction": company.get("jurisdiction_code") or company.get("jurisdiction"),
                    "legal_name": company.get("legal_name") or company.get("name"),
                    "url": company.get("entity_url") or company.get("url"),
                }
            )
        return normalized

    async def _post(self, url: str, body: dict[str, object]) -> dict[str, Any]:
        client = self._client()
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, ProviderError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        ):
            with attempt:
                response = await client.post(url, json=body, headers=self._headers())
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProviderError(f"govfiles upstream {response.status_code}")
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
        raise ProviderError("govfiles retries exhausted")

    def _headers(self) -> dict[str, str]:
        if self._settings.govfiles_api_key is None:
            raise ProviderError("GOVFILES_API_KEY is not configured")
        return {"X-API-Key": self._settings.govfiles_api_key.get_secret_value()}

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": "AcquiScout/0.1 (+approved-source research)"},
            )
        return self._http

    async def resolve(self, candidate: Candidate) -> str | None:
        if candidate.crawl_url is not None:
            return str(candidate.crawl_url)
        jurisdiction = candidate.registry_jurisdiction
        if not jurisdiction:
            return None
        key = fixture_key(
            self.name,
            f"{jurisdiction}:{candidate.identifier}",
            {"kind": "registry_url"},
            GOVFILES_VERSION,
        )
        try:
            payload = load_fixture(self._settings.dev_fixtures_dir, "registry_url", self.name, key)
        except FixtureMissing:
            payload = None
        if isinstance(payload, dict):
            return cast(str | None, payload.get("url"))
        if self._settings.use_fixtures:
            return None
        try:
            response = await self._client().get(
                f"{self._base_url()}/v2/companies/{jurisdiction}/{candidate.identifier}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return cast(str | None, response.json().get("entity_url"))
        except (httpx.HTTPError, ProviderError):
            return None

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


__all__ = ["DEFAULT_BASE_URL", "GOVFILES_VERSION", "GovFilesSearchProvider"]
