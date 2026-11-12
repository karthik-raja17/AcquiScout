import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.sources import PolicyViolation, Scraper, SourcePolicy
from acquiscout.sources.circuit import HostCircuitBreaker
from acquiscout.sources.policy import is_approved_registry_url
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


def test_approved_registry_hosts_cover_apex_and_subdomains() -> None:
    for url in (
        "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResultDetail",
        "https://efile.sunbiz.org/",
        "https://www.myfloridalicense.com/",
        "https://www2.myfloridalicense.com/",
        "https://dos.fl.gov/",
    ):
        assert is_approved_registry_url(url) is True
    assert is_approved_registry_url("https://example.com/") is False


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


@pytest.mark.asyncio
async def test_cloudflare_challenge_is_classified_as_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "Title: Just a moment...\n\n"
                "Warning: Target URL returned error 403: Forbidden\n\n"
                "Markdown Content:\nEnable JavaScript and cookies to continue\n"
            ),
        )

    scraper = Scraper(make_settings(), http=make_http(handler), cache=MemoryCache())
    result = await scraper.fetch("https://search.sunbiz.org/Inquiry/CorporationSearch")

    assert result.ok is False
    assert result.status == "blocked"
    assert result.reason == "cloudflare_challenge"
    assert result.target_status == 403
    assert result.title == "Just a moment..."
    assert result.circuit_failure is True


@pytest.mark.asyncio
async def test_rate_limit_is_classified_and_retained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")

    scraper = Scraper(make_settings(), http=make_http(handler), cache=MemoryCache())
    result = await scraper.fetch("https://example.com/company")

    assert result.ok is False
    assert result.status == "rate_limited"
    assert result.reason == "http_429"
    assert result.retry_after_seconds == 0.0
    assert result.circuit_failure is True


@pytest.mark.asyncio
async def test_circuit_opens_and_skips_same_host() -> None:
    calls: list[str] = []
    now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            text="Title: Just a moment...\nWarning: Target URL returned error 403: Forbidden\n",
        )

    breaker = HostCircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    scraper = Scraper(
        make_settings(),
        http=make_http(handler),
        cache=MemoryCache(),
        circuit_breaker=breaker,
    )

    first = await scraper.fetch("https://search.sunbiz.org/one")
    second = await scraper.fetch("https://search.sunbiz.org/two")

    assert first.status == "blocked"
    assert second.status == "blocked"
    assert second.reason == "host_circuit_open"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_circuit_half_open_probes_once_then_closes() -> None:
    now = [0.0]
    breaker = HostCircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    url = "https://search.sunbiz.org/one"

    assert (await breaker.before(url)).allowed is True
    await breaker.record(url, failed=True)
    assert (await breaker.before(url)).allowed is False

    now[0] = 11.0
    probe = await breaker.before(url)
    assert probe.allowed is True
    assert probe.probe is True
    assert (await breaker.before(url)).allowed is False

    await breaker.record(url, failed=False)
    assert (await breaker.before(url)).allowed is True
