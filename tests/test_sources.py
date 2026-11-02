import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.sources import PolicyViolation, Scraper, SourcePolicy
from acquiscout.sources.scrape import EXTRACTOR_VERSION

LONG_TEXT = "Title company registry page. " * 40


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.data[key] = value


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def make_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_policy_allows_public_registry() -> None:
    assert SourcePolicy().is_allowed("https://www.sunbiz.org/records")


def test_policy_blocks_login_and_paywall_hosts() -> None:
    policy = SourcePolicy()
    for url in (
        "https://www.linkedin.com/company/acme",
        "https://sub.wsj.com/article",
        "https://twitter.com/acme",
    ):
        assert policy.is_allowed(url) is False


def test_policy_blocks_ssrf_and_bad_schemes() -> None:
    policy = SourcePolicy()
    for url in ("http://localhost:8000/admin", "http://127.0.0.1/", "file:///etc/passwd"):
        assert policy.is_allowed(url) is False


def test_policy_enforce_raises() -> None:
    with pytest.raises(PolicyViolation):
        SourcePolicy().enforce("https://www.facebook.com/acme")


@pytest.mark.asyncio
async def test_jina_success_uses_free_reader() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=LONG_TEXT)

    scraper = Scraper(make_settings(), http=make_http(handler), cache=MemoryCache())
    result = await scraper.fetch("https://example.com/company")

    assert result.ok is True
    assert result.backend == "jina"
    assert result.cached is False
    assert len(calls) == 1
    assert calls[0].startswith("https://r.jina.ai/")


@pytest.mark.asyncio
async def test_cache_hit_skips_network_and_key_has_version() -> None:
    calls: list[str] = []
    cache = MemoryCache()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=LONG_TEXT)

    scraper = Scraper(make_settings(), http=make_http(handler), cache=cache)
    await scraper.fetch("https://example.com/company")
    second = await scraper.fetch("https://example.com/company")

    assert second.cached is True
    assert len(calls) == 1
    assert f"scrape:{EXTRACTOR_VERSION}:" in next(iter(cache.data))


@pytest.mark.asyncio
async def test_low_quality_without_fallback_does_not_spend_credits() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="short")

    scraper = Scraper(
        make_settings(scrape_fallback_to_firecrawl=False),
        http=make_http(handler),
        cache=MemoryCache(),
    )
    result = await scraper.fetch("https://example.com/company")

    assert result.ok is False
    assert result.reason == "jina_low_quality"
    assert all("firecrawl" not in call for call in calls)


@pytest.mark.asyncio
async def test_fallback_uses_firecrawl_when_enabled() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "r.jina.ai" in str(request.url):
            return httpx.Response(200, text="blocked")
        return httpx.Response(200, json={"success": True, "data": {"markdown": LONG_TEXT}})

    scraper = Scraper(
        make_settings(scrape_fallback_to_firecrawl=True, firecrawl_api_key="test-key"),
        http=make_http(handler),
        cache=MemoryCache(),
    )
    result = await scraper.fetch("https://example.com/company")

    assert result.ok is True
    assert result.backend == "firecrawl"
    assert any("api.firecrawl.dev" in call for call in calls)


@pytest.mark.asyncio
async def test_policy_blocks_before_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=LONG_TEXT)

    scraper = Scraper(make_settings(), http=make_http(handler), cache=MemoryCache())
    with pytest.raises(PolicyViolation):
        await scraper.fetch("https://www.linkedin.com/company/acme")
    assert calls == []
