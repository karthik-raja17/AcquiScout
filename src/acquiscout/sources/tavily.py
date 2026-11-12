"""Tavily web search with cache, retries, and dev fixture support."""

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
from acquiscout.schemas import SearchTask
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.fixtures import fixture_key, load_fixture, save_fixture
from acquiscout.sources.providers import ProviderError

TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_VERSION = "tavily_v1"


class TavilySearchProvider:
    name = "tavily_web"

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
        return {
            "max_results": self._settings.search_max_results,
            "search_depth": "basic",
            **task.params,
        }

    async def search(self, task: SearchTask) -> list[dict[str, Any]]:
        results, _ = await self.search_raw(task)
        return results

    async def search_raw(
        self, task: SearchTask
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        params = self._params(task)
        key = fixture_key(self.name, task.query, params, TAVILY_VERSION)

        if self._settings.use_fixtures:
            payload = load_fixture(self._settings.dev_fixtures_dir, "search", self.name, key)
            return cast(list[dict[str, Any]], payload.get("results", [])), payload

        if self._cache is not None:
            cached = self._cache.get(self.name, task.query, params, TAVILY_VERSION)
            if isinstance(cached, dict):
                return cast(list[dict[str, Any]], cached.get("results", [])), cached

        results, raw = await self._live(task, params)
        if self._cache is not None:
            self._cache.set(self.name, task.query, params, TAVILY_VERSION, {"results": results})
        if self._settings.capture_search:
            save_fixture(
                self._settings.dev_fixtures_dir,
                "search",
                self.name,
                key,
                {"results": results},
            )
        return results, raw

    async def _live(
        self, task: SearchTask, params: dict[str, object]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self._settings.tavily_api_key is None:
            raise ProviderError("TAVILY_API_KEY is not configured")
        body = {
            "api_key": self._settings.tavily_api_key.get_secret_value(),
            "query": task.query,
            **params,
            "include_answer": False,
        }
        payload = await self._post(body)
        raw_results = payload.get("results", [])
        results = [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "snippet": item.get("content") or item.get("snippet"),
                "score": item.get("score"),
            }
            for item in raw_results
            if item.get("url")
        ]
        return results, payload

    async def _post(self, body: dict[str, object]) -> dict[str, Any]:
        client = self._client()
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, ProviderError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        ):
            with attempt:
                response = await client.post(TAVILY_ENDPOINT, json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProviderError(f"tavily upstream {response.status_code}")
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
        raise ProviderError("tavily retries exhausted")

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": "AcquiScout/0.1 (+approved-source research)"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


__all__ = ["TAVILY_ENDPOINT", "TAVILY_VERSION", "TavilySearchProvider"]
