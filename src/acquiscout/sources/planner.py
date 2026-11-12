"""Groq-backed search planner with validation, cache, and fixtures."""

from __future__ import annotations

import json
import re
from typing import Any

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
from acquiscout.sources.providers import PlannerError, ProviderError

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
ALLOWED_NODES = {"tavily_web", "govfiles_registry"}
NODE_ALIASES = {
    "tavily": "tavily_web",
    "web": "tavily_web",
    "tavily_web": "tavily_web",
    "govfiles": "govfiles_registry",
    "registry": "govfiles_registry",
    "govfiles_registry": "govfiles_registry",
}


def build_prompt(icp: dict[str, Any], hints: dict[str, Any], plan_size: int) -> str:
    schema = {
        "tasks": [
            {
                "node": "tavily_web",
                "query": "Hillsborough County title company",
                "params": {"max_results": 5},
            },
            {
                "node": "govfiles_registry",
                "query": "Hillsborough County title company Sunbiz",
                "params": {"max_results": 5},
            },
        ]
    }
    return (
        "You plan web and public-registry searches for an M&A sourcing pipeline.\n"
        "Return JSON only, matching this shape:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        f"Generate exactly {plan_size} tasks. `node` must be exactly either "
        '`"tavily_web"` or `"govfiles_registry"` — never a combined string. '
        "Use tavily_web for company/website discovery and govfiles_registry for "
        "public registry discovery. Alternate them: two tavily_web tasks and two "
        "govfiles_registry tasks when generating four. Queries must be short, "
        "specific, and include the geography. For govfiles_registry, queries are "
        "matched against company names, so use sector + geography only (for example, "
        "'Hillsborough title company'); do not include Sunbiz, registered agent, or "
        "registry vendor names.\n\n"
        f"ICP:\n{json.dumps(icp, indent=2, sort_keys=True)}\n\n"
        f"Search vocabulary hints:\n{json.dumps(hints, indent=2, sort_keys=True)}\n"
    )


def _extract_tasks(data: Any, plan_size: int) -> list[SearchTask]:
    if isinstance(data, dict):
        raw = data.get("tasks") or data.get("search_plan") or data.get("queries") or []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []
    tasks: list[SearchTask] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        node = str(item.get("node") or item.get("provider") or "").lower()
        if "tavily" in node and "govfiles" in node:
            node = "tavily_web" if index % 2 == 0 else "govfiles_registry"
        node = NODE_ALIASES.get(node, node)
        query = str(item.get("query") or "").strip()
        if node not in ALLOWED_NODES or not query:
            continue
        params = item.get("params")
        tasks.append(
            SearchTask(
                task_id=f"{node}-{index}",
                node=node,  # type: ignore[arg-type]
                query=query,
                params=params if isinstance(params, dict) else {},
                search_plan_index=len(tasks),
            )
        )
        if len(tasks) >= plan_size:
            break
    if not tasks:
        raise PlannerError("planner returned no valid tasks")
    return tasks


def _parse_content(content: str, plan_size: int) -> list[SearchTask]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"planner returned invalid JSON: {exc}") from exc
    return _extract_tasks(data, plan_size)


def fallback_plan(icp: dict[str, Any], hints: dict[str, Any], plan_size: int) -> list[SearchTask]:
    sectors = list(icp.get("sectors") or hints.get("sector_terms", {}).keys()) or [
        "title company",
        "property management",
    ]
    sector_terms: list[str] = []
    for sector in sectors:
        terms = hints.get("sector_terms", {}).get(sector)
        if terms:
            sector_terms.extend(terms[:2])
        else:
            sector_terms.append(str(sector).replace("_", " "))
    counties = list(icp.get("counties") or hints.get("geography_terms", [])[:3]) or [
        "Tampa",
        "Hillsborough County",
        "Pinellas County",
    ]
    tasks: list[SearchTask] = []
    for index in range(plan_size):
        sector = sector_terms[index % len(sector_terms)]
        county = counties[index % len(counties)]
        if index % 2 == 0:
            node = "tavily_web"
            query = f"{sector} {county} Florida"
        else:
            node = "govfiles_registry"
            query = f"{county} {sector}"
        tasks.append(
            SearchTask(
                task_id=f"{node}-{index}",
                node=node,  # type: ignore[arg-type]
                query=query,
                params={},
                search_plan_index=index,
            )
        )
    return tasks


class GroqPlanner:
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
        self.last_raw: dict[str, Any] | None = None
        self.last_usage: dict[str, Any] | None = None

    def _version(self) -> str:
        return (
            f"{self._settings.groq_model}:"
            f"{self._settings.search_prompt_version}:"
            f"{self._settings.search_hints_version}"
        )

    async def plan(self, icp: dict[str, Any], hints: dict[str, Any]) -> list[SearchTask]:
        prompt = build_prompt(icp, hints, self._settings.search_plan_size)
        version = self._version()
        key = fixture_key("groq", prompt, {}, version)
        self.last_raw = None
        self.last_usage = None

        if self._settings.use_fixtures:
            payload = load_fixture(self._settings.dev_fixtures_dir, "planner", "groq", key)
            self.last_raw = {
                "source": "fixture",
                "prompt": prompt,
                "model": self._settings.groq_model,
                "payload": payload,
            }
            return _extract_tasks(payload, self._settings.search_plan_size)

        if self._cache is not None:
            cached = self._cache.get("groq", prompt, {}, version)
            if cached is not None:
                self.last_raw = {
                    "source": "cache",
                    "prompt": prompt,
                    "model": self._settings.groq_model,
                    "payload": cached,
                }
                return _extract_tasks(cached, self._settings.search_plan_size)

        content, usage = await self._live(prompt)
        tasks = _parse_content(content, self._settings.search_plan_size)
        self.last_raw = {
            "source": "live",
            "prompt": prompt,
            "model": self._settings.groq_model,
            "response": content,
            "usage": usage,
        }
        self.last_usage = usage
        payload = {
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "raw": content,
            "usage": usage,
        }
        if self._cache is not None:
            self._cache.set("groq", prompt, {}, version, payload)
        if self._settings.capture_planner:
            save_fixture(self._settings.dev_fixtures_dir, "planner", "groq", key, payload)
        return tasks

    async def _live(self, prompt: str) -> tuple[str, dict[str, Any] | None]:
        if self._settings.groq_api_key is None:
            raise ProviderError("GROQ_API_KEY is not configured")
        client = self._client()
        body = {
            "model": self._settings.groq_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You are a deterministic search planner. Return JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.groq_api_key.get_secret_value()}"}
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, ProviderError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=8),
            reraise=True,
        ):
            with attempt:
                response = await client.post(GROQ_ENDPOINT, json=body, headers=headers)
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProviderError(f"groq upstream {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                content = str(payload["choices"][0]["message"]["content"])
                usage = payload.get("usage")
                return content, usage if isinstance(usage, dict) else None
        raise ProviderError("groq retries exhausted")

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


__all__ = [
    "ALLOWED_NODES",
    "GROQ_ENDPOINT",
    "GroqPlanner",
    "build_prompt",
    "fallback_plan",
]
