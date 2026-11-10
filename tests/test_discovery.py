from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.dev.freeze_from_checkpoint import freeze
from acquiscout.pipeline.discovery_graph import (
    memory_checkpointer,
    run_discovery,
    sqlite_checkpointer,
)
from acquiscout.pipeline.export import export_run
from acquiscout.pipeline.icp import SEARCH_HINTS, build_icp
from acquiscout.pipeline.nodes import DiscoveryDeps, canonicalize_url
from acquiscout.pipeline.runlog import append_discovery_run
from acquiscout.schemas import Candidate, QueryHit, SearchTask
from acquiscout.sources.cache import SQLiteCache, cache_key
from acquiscout.sources.dlq import push_failure
from acquiscout.sources.fixtures import FixtureMissing, fixture_key, load_fixture, save_fixture
from acquiscout.sources.planner import GroqPlanner, _extract_tasks, build_prompt
from acquiscout.sources.policy import SourcePolicy
from acquiscout.sources.tavily import TAVILY_VERSION, TavilySearchProvider


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "capture_search": False,
        "capture_planner": False,
        "use_fixtures": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def task(index: int, node: str, query: str) -> SearchTask:
    return SearchTask(
        task_id=f"{node}-{index}",
        node=node,  # type: ignore[arg-type]
        query=query,
        search_plan_index=index,
    )


class FakePlanner:
    def __init__(
        self, tasks: list[SearchTask] | None = None, error: Exception | None = None
    ) -> None:
        self.tasks = tasks or []
        self.error = error

    async def plan(self, icp: dict[str, Any], hints: dict[str, Any]) -> list[SearchTask]:
        if self.error is not None:
            raise self.error
        return self.tasks

    async def aclose(self) -> None:
        return None


class FakeProvider:
    def __init__(
        self,
        name: str,
        results: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.results = results or []
        self.error = error
        self.calls = 0

    async def search(self, task: SearchTask) -> list[dict[str, Any]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results

    async def aclose(self) -> None:
        return None


class FakeResolver:
    def __init__(self, url: str | None = None) -> None:
        self.url = url

    async def resolve(self, candidate: Candidate) -> str | None:
        return self.url


def deps_for(
    planner: FakePlanner,
    tavily: FakeProvider,
    govfiles: FakeProvider,
    resolver: FakeResolver | None = None,
    **overrides: object,
) -> DiscoveryDeps:
    return DiscoveryDeps(
        settings=make_settings(**overrides),
        planner=planner,
        tavily=tavily,
        govfiles=govfiles,
        resolver=resolver,
    )


@pytest.mark.asyncio
async def test_successful_run_dedups_and_resolves() -> None:
    planner = FakePlanner(
        [task(0, "tavily_web", "title tampa"), task(1, "govfiles_registry", "registry")]
    )
    tavily = FakeProvider(
        "tavily_web",
        [
            {"url": "https://www.example.com/about?utm_source=x", "title": "A"},
            {"url": "https://example.com/about", "title": "A2"},
            {"url": "https://example.com/team", "title": "Team"},
        ],
    )
    govfiles = FakeProvider(
        "govfiles_registry", [{"entity_id": "P123", "legal_name": "Example LLC"}]
    )
    deps = deps_for(planner, tavily, govfiles, FakeResolver("https://sunbiz.org/P123"))

    final = await run_discovery(deps, checkpointer=memory_checkpointer(), run_id="run-ok")

    assert final["run_status"] == "completed"
    assert final["search_failure_count"] == 0
    assert final["raw_search_results"] == []
    assert len(final["deduplicated_candidates"]) == 3
    urls = [
        candidate.identifier
        for candidate in final["deduplicated_candidates"]
        if candidate.identifier_kind == "url"
    ]
    assert "https://example.com/about" in urls
    assert all("utm_source" not in url for url in urls)
    registry = next(
        candidate
        for candidate in final["deduplicated_candidates"]
        if candidate.identifier_kind == "registry_id"
    )
    assert registry.resolution_status == "resolved"
    assert registry.crawl_url == "https://sunbiz.org/P123"


@pytest.mark.asyncio
async def test_total_failure_accumulates_and_aborts() -> None:
    planner = FakePlanner(
        [
            task(0, "tavily_web", "q0"),
            task(1, "tavily_web", "q1"),
            task(2, "govfiles_registry", "q2"),
        ]
    )
    tavily = FakeProvider("tavily_web", error=TimeoutError("down"))
    govfiles = FakeProvider("govfiles_registry", error=TimeoutError("down"))
    deps = deps_for(planner, tavily, govfiles)

    final = await run_discovery(deps, checkpointer=memory_checkpointer(), run_id="run-abort")

    assert final["run_status"] == "aborted"
    assert final["search_failure_count"] == 3
    assert final["raw_search_results"] == []
    assert final["deduplicated_candidates"] == []
    assert "3/3" in (final["abort_reason"] or "")


@pytest.mark.asyncio
async def test_under_threshold_continues_degraded() -> None:
    planner = FakePlanner(
        [
            task(0, "tavily_web", "q0"),
            task(1, "tavily_web", "q1"),
            task(2, "govfiles_registry", "q2"),
            task(3, "govfiles_registry", "q3"),
        ]
    )
    tavily = FakeProvider("tavily_web", error=TimeoutError("down"))
    govfiles = FakeProvider("govfiles_registry", [{"entity_id": "P1", "legal_name": "Example"}])
    deps = deps_for(planner, tavily, govfiles)

    final = await run_discovery(deps, checkpointer=memory_checkpointer(), run_id="run-degraded")

    assert final["run_status"] == "completed"
    assert final["search_failure_count"] == 2
    assert len(final["deduplicated_candidates"]) == 1


@pytest.mark.asyncio
async def test_empty_plan_aborts_with_reason() -> None:
    deps = deps_for(FakePlanner([]), FakeProvider("tavily_web"), FakeProvider("govfiles_registry"))

    final = await run_discovery(deps, checkpointer=memory_checkpointer(), run_id="run-empty")

    assert final["run_status"] == "aborted"
    assert final["abort_reason"] == "Search plan was empty"


@pytest.mark.asyncio
async def test_planner_failure_uses_fallback_plan() -> None:
    planner = FakePlanner(error=RuntimeError("groq down"))
    tavily = FakeProvider("tavily_web", [{"url": "https://example.com", "title": "Example"}])
    govfiles = FakeProvider("govfiles_registry", [{"entity_id": "P1", "legal_name": "Example"}])
    deps = deps_for(planner, tavily, govfiles)

    final = await run_discovery(deps, checkpointer=memory_checkpointer(), run_id="run-fallback")

    assert final["run_status"] == "completed"
    assert len(final["search_plan"]) == deps.settings.search_plan_size
    assert any(error["provider"] == "planner" for error in final["provider_errors"])


def test_cache_key_is_provider_scoped() -> None:
    assert cache_key("tavily_web", "q", {}, "v1") != cache_key("govfiles_registry", "q", {}, "v1")


def test_sqlite_cache_round_trip(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "cache.db", ttl_seconds=60)
    assert cache.get("tavily_web", "q", {"n": 1}, "v1") is None
    cache.set("tavily_web", "q", {"n": 1}, "v1", {"results": [1]})
    assert cache.get("tavily_web", "q", {"n": 1}, "v1") == {"results": [1]}
    assert cache.get("govfiles_registry", "q", {"n": 1}, "v1") is None


def test_fixture_round_trip_and_missing(tmp_path: Path) -> None:
    save_fixture(tmp_path, "search", "tavily_web", "k", {"results": [1]})
    assert load_fixture(tmp_path, "search", "tavily_web", "k") == {"results": [1]}
    with pytest.raises(FixtureMissing):
        load_fixture(tmp_path, "search", "tavily_web", "missing")


@pytest.mark.asyncio
async def test_fixture_replay_never_calls_http(tmp_path: Path) -> None:
    settings = make_settings(use_fixtures=True, dev_fixtures_dir=tmp_path)
    search_task = task(0, "tavily_web", "q")
    params = {"max_results": settings.search_max_results, "search_depth": "basic"}
    key = fixture_key("tavily_web", "q", params, TAVILY_VERSION)
    save_fixture(
        tmp_path,
        "search",
        "tavily_web",
        key,
        {"results": [{"url": "https://example.com", "title": "Example"}]},
    )

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be used in replay mode")

    http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    provider = TavilySearchProvider(settings, http=http)
    results = await provider.search(search_task)
    await provider.aclose()

    assert results == [{"url": "https://example.com", "title": "Example"}]


@pytest.mark.asyncio
async def test_planner_fixture_replay(tmp_path: Path) -> None:
    settings = make_settings(use_fixtures=True, dev_fixtures_dir=tmp_path)
    icp = build_icp(settings)
    prompt = build_prompt(icp, SEARCH_HINTS, settings.search_plan_size)
    version = (
        f"{settings.groq_model}:{settings.search_prompt_version}:{settings.search_hints_version}"
    )
    key = fixture_key("groq", prompt, {}, version)
    save_fixture(
        tmp_path,
        "planner",
        "groq",
        key,
        {
            "tasks": [
                {
                    "task_id": "t",
                    "node": "tavily_web",
                    "query": "q",
                    "params": {},
                    "search_plan_index": 0,
                }
            ]
        },
    )
    planner = GroqPlanner(settings)

    tasks = await planner.plan(icp, SEARCH_HINTS)

    assert len(tasks) == 1
    assert tasks[0].query == "q"


@pytest.mark.asyncio
async def test_planner_capture_writes_fixture(tmp_path: Path) -> None:
    settings = make_settings(
        capture_planner=True, dev_fixtures_dir=tmp_path, groq_api_key="test-key"
    )
    content = json.dumps(
        {
            "tasks": [
                {"node": "tavily_web", "query": "q", "params": {}},
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = GroqPlanner(settings, http=http)
    tasks = await planner.plan(build_icp(settings), SEARCH_HINTS)
    await planner.aclose()

    assert tasks[0].query == "q"
    assert list((tmp_path / "planner" / "groq").glob("*.json"))


def test_planner_repairs_combined_node_literal() -> None:
    tasks = _extract_tasks(
        {
            "tasks": [
                {
                    "node": "tavily_web | govfiles_registry",
                    "query": "q0",
                    "params": {},
                },
                {
                    "node": "tavily_web | govfiles_registry",
                    "query": "q1",
                    "params": {},
                },
            ]
        },
        2,
    )

    assert [task.node for task in tasks] == ["tavily_web", "govfiles_registry"]


def test_policy_classifies_and_keeps_ssrf_guard() -> None:
    policy = SourcePolicy()
    assert (
        policy.classify_candidate(
            Candidate(
                identifier="https://www.linkedin.com/company/x",
                identifier_kind="url",
                source_type="web",
                provider="tavily_web",
            )
        ).allowed
        is False
    )
    assert (
        policy.classify_candidate(
            Candidate(
                identifier="http://127.0.0.1/admin",
                identifier_kind="url",
                source_type="web",
                provider="tavily_web",
            )
        ).allowed
        is False
    )
    assert (
        policy.classify_candidate(
            Candidate(
                identifier="https://example.com/brochure.pdf",
                identifier_kind="url",
                source_type="web",
                provider="tavily_web",
            )
        ).allowed
        is False
    )
    registry_pdf = policy.classify_candidate(
        Candidate(
            identifier="https://search.sunbiz.org/filing.pdf",
            identifier_kind="url",
            source_type="web",
            provider="tavily_web",
        )
    )
    assert registry_pdf.allowed is True
    assert registry_pdf.classification == "registry_pdf"
    assert (
        policy.classify_candidate(
            Candidate(
                identifier="P123",
                identifier_kind="registry_id",
                source_type="registry",
                provider="govfiles_registry",
            )
        ).allowed
        is True
    )


@pytest.mark.asyncio
async def test_dlq_uses_jsonl_when_redis_fails(tmp_path: Path) -> None:
    class BrokenRedis:
        async def lpush(self, key: str, value: str) -> None:
            raise ConnectionError("redis down")

        async def ltrim(self, key: str, start: int, end: int) -> None:
            raise AssertionError("must not be called")

    path = tmp_path / "dlq.jsonl"
    ok = await push_failure(
        {"provider": "tavily_web", "query": "q", "error": "boom"},
        redis_client=BrokenRedis(),
        fallback_path=path,
    )

    assert ok is True
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["provider"] == "tavily_web"
    assert payload["error"] == "boom"


def test_canonicalize_url_strips_tracking_and_fragment() -> None:
    assert (
        canonicalize_url("https://WWW.Example.com/about/?utm_source=x&b=2&a=1#top")
        == "https://example.com/about?a=1&b=2"
    )


def test_query_hit_and_candidate_are_checkpointable() -> None:
    candidate = Candidate(
        identifier="https://example.com",
        identifier_kind="url",
        source_type="web",
        provider="tavily_web",
        found_by=[QueryHit(query="q", search_plan_index=0, provider="tavily_web")],
    )
    assert candidate.crawl_url is None


@pytest.mark.asyncio
async def test_freeze_from_checkpoint_writes_fixtures(tmp_path: Path) -> None:
    settings = make_settings(
        dev_fixtures_dir=tmp_path / "fixtures",
        checkpoint_db_path=tmp_path / "checkpoints.db",
        search_cache_db_path=tmp_path / "cache.db",
        dlq_fallback_path=tmp_path / "dlq.jsonl",
    )
    planner = FakePlanner([task(0, "tavily_web", "q")])
    tavily = FakeProvider("tavily_web", [{"url": "https://example.com", "title": "Example"}])
    deps = DiscoveryDeps(
        settings=settings,
        planner=planner,
        tavily=tavily,
        govfiles=FakeProvider("govfiles_registry"),
    )
    saver, conn = await sqlite_checkpointer(settings)
    try:
        final = await run_discovery(deps, checkpointer=saver, run_id="freeze-run")
    finally:
        await conn.close()

    assert final["run_status"] == "completed"
    written = freeze("freeze-run", settings)
    assert written == 1
    assert list((tmp_path / "fixtures").rglob("*.json"))


def test_discovery_run_log_is_one_row_per_run(tmp_path: Path) -> None:
    settings = make_settings(discovery_run_log_path=tmp_path / "runs.md")
    final = {
        "run_id": "run-log",
        "run_status": "completed",
        "search_plan": [],
        "search_failure_count": 0,
        "deduplicated_candidates": [],
        "provider_errors": [],
        "abort_reason": None,
    }

    path = append_discovery_run(final, settings)
    first = path.read_text(encoding="utf-8")
    append_discovery_run(final, settings)
    second = path.read_text(encoding="utf-8")

    assert first.startswith("# Discovery Runs Log")
    assert first.count("| run-log |") == 1
    assert second.count("| run-log |") == 1


def test_export_run_writes_completed_only(tmp_path: Path) -> None:
    settings = make_settings()
    completed = {
        "run_id": "run-export",
        "run_status": "completed",
        "target_market": "tampa_bay",
        "target_sector": "title_company",
        "icp": {},
        "search_plan": [task(0, "tavily_web", "q")],
        "search_failure_count": 1,
        "provider_errors": [{"provider": "govfiles_registry"}],
        "deduplicated_candidates": [
            Candidate(
                identifier="https://example.com",
                identifier_kind="url",
                source_type="web",
                provider="tavily_web",
            )
        ],
    }

    path = export_run(completed, settings, tmp_path)

    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-export"
    assert payload["status"] == "completed"
    assert payload["candidates"][0]["identifier"] == "https://example.com"

    aborted = {**completed, "run_status": "aborted"}
    assert export_run(aborted, settings, tmp_path) is None
