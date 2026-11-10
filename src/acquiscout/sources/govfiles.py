"""GovFiles registry adapter.

The live endpoint is configurable and unverified. Fixture replay is the
supported development path until the provider contract is confirmed.
"""

from __future__ import annotations

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
from acquiscout.sources.fixtures import FixtureMissing, fixture_key, load_fixture, save_fixture
from acquiscout.sources.providers import ProviderError

GOVFILES_VERSION = "govfiles_v1"


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

    def _params(self, task: SearchTask) -> dict[str, object]:
        return {"max_results": self._settings.search_max_results, **task.params}

    async def search(self, task: SearchTask) -> list[dict[str, Any]]:
        params = self._params(task)
        key = fixture_key(self.name, task.query, params, GOVFILES_VERSION)

        if self._settings.use_fixtures:
            payload = load_fixture(self._settings.dev_fixtures_dir, "search", self.name, key)
            return cast(list[dict[str, Any]], payload.get("results", []))

        if self._cache is not None:
            cached = self._cache.get(self.name, task.query, params, GOVFILES_VERSION)
            if isinstance(cached, dict):
                return cast(list[dict[str, Any]], cached.get("results", []))

        results = await self._live(task, params)
        if self._cache is not None:
            self._cache.set(self.name, task.query, params, GOVFILES_VERSION, {"results": results})
        if self._settings.capture_search:
            save_fixture(
                self._settings.dev_fixtures_dir,
                "search",
                self.name,
                key,
                {"results": results},
            )
        return results

    async def _live(self, task: SearchTask, params: dict[str, object]) -> list[dict[str, Any]]:
        base = self._settings.govfiles_base_url.rstrip("/")
        if not base:
            raise ProviderError(
                "GOVFILES_BASE_URL is not configured; use fixture replay for GovFiles"
            )
        body = {"query": task.query, **params}
        payload = await self._post(f"{base}/entities/search", body)
        raw_results = payload.get("results", payload.get("data", []))
        return [
            {
                "entity_id": str(
                    item.get("entity_id") or item.get("id") or item.get("company_number") or ""
                ),
                "legal_name": item.get("legal_name") or item.get("name"),
                "url": item.get("url") or item.get("source_url") or item.get("profile_url"),
                "jurisdiction": item.get("jurisdiction"),
            }
            for item in raw_results
            if item.get("entity_id") or item.get("id") or item.get("company_number")
        ]

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
            return {}
        return {"Authorization": f"Bearer {self._settings.govfiles_api_key.get_secret_value()}"}

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
        key = fixture_key(
            self.name, candidate.identifier, {"kind": "registry_url"}, GOVFILES_VERSION
        )
        try:
            payload = load_fixture(self._settings.dev_fixtures_dir, "registry_url", self.name, key)
        except FixtureMissing:
            payload = None
        if payload is not None:
            return cast(str | None, payload.get("url"))
        if self._settings.use_fixtures or self._settings.govfiles_base_url == "":
            return None
        base = self._settings.govfiles_base_url.rstrip("/")
        try:
            response = await self._client().get(
                f"{base}/entities/{candidate.identifier}", headers=self._headers()
            )
            response.raise_for_status()
            return cast(str | None, response.json().get("url"))
        except httpx.HTTPError:
            return None

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


__all__ = ["GOVFILES_VERSION", "GovFilesSearchProvider"]
