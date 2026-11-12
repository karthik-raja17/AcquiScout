from __future__ import annotations

import httpx
import pytest

from acquiscout.eval.discovery import (
    DiscoveryLabel,
    DiscoverySample,
    FetchCheck,
    check_official_url,
    check_url,
    evaluate,
    format_report,
    load_samples,
    root_domain,
)
from acquiscout.sources.scrape import ScrapeResult


def sample(url: str, label: DiscoveryLabel) -> DiscoverySample:
    return DiscoverySample(url=url, label=label)


def check(url: str, ok: bool = True) -> FetchCheck:
    return FetchCheck(url=url, ok=ok, status_code=200 if ok else 500, text_chars=800 if ok else 0)


def test_load_samples_from_workbook_file() -> None:
    samples = load_samples("data/raw/discovery_eval_v1.json")

    assert len(samples) == 40
    assert sum(1 for item in samples if item.label is DiscoveryLabel.REGISTRY) == 8


def test_root_domain_uses_registered_domain() -> None:
    assert root_domain("https://www.example.co.uk/path") == "example.co.uk"
    assert root_domain("https://sub.example.com/path") == "example.com"


def test_metrics_on_mixed_sample() -> None:
    samples = [
        sample("https://a.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://b.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://c.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://d.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://e.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://f.com", DiscoveryLabel.DIRECTORY),
        sample("https://g.com", DiscoveryLabel.DIRECTORY),
        sample("https://h.gov", DiscoveryLabel.GOVERNMENT),
        sample("https://sunbiz.example", DiscoveryLabel.REGISTRY),
        sample("https://sunbiz2.example", DiscoveryLabel.REGISTRY),
    ]
    checks = [check(item.url) for item in samples if item.label is not DiscoveryLabel.REGISTRY]

    metrics = evaluate(samples, checks)

    assert metrics.web_sample_size == 8
    assert metrics.registry_count == 2
    assert metrics.official_company == 5
    assert metrics.official_fetch_ok == 5
    assert metrics.crawl_worthy_precision == 0.625
    assert metrics.non_target_share == 0.375
    assert metrics.gates["fetch_success"] is True
    assert metrics.gates["crawl_worthy"] is False


def test_metrics_pass_gates_at_eighty_percent() -> None:
    samples = [
        *[sample(f"https://company{i}.com", DiscoveryLabel.OFFICIAL_COMPANY) for i in range(8)],
        sample("https://dir1.com", DiscoveryLabel.DIRECTORY),
        sample("https://dir2.com", DiscoveryLabel.DIRECTORY),
    ]
    checks = [check(item.url) for item in samples]

    metrics = evaluate(samples, checks)

    assert metrics.crawl_worthy_precision == 0.8
    assert metrics.gates == {"fetch_success": True, "crawl_worthy": True}


def test_fetch_failure_reduces_official_fetch_success() -> None:
    samples = [
        sample("https://ok.com", DiscoveryLabel.OFFICIAL_COMPANY),
        sample("https://broken.com", DiscoveryLabel.OFFICIAL_COMPANY),
    ]
    checks = [check("https://ok.com"), check("https://broken.com", ok=False)]

    metrics = evaluate(samples, checks)

    assert metrics.official_company == 2
    assert metrics.official_fetch_ok == 1
    assert metrics.official_fetch_success_rate == 0.5
    assert metrics.crawl_worthy_precision == 1.0
    assert metrics.gates["fetch_success"] is False


@pytest.mark.asyncio
async def test_check_url_extracts_html_text() -> None:
    html = "<html><body><p>" + ("Company profile text. " * 80) + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_url("https://example.com", client=client)

    assert result.ok is True
    assert result.text_chars >= 400


@pytest.mark.asyncio
async def test_check_url_rejects_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_url("https://example.com/missing", client=client)

    assert result.ok is False
    assert result.reason == "http_404"


@pytest.mark.asyncio
async def test_check_official_url_uses_scraper_result() -> None:
    class FakeScraper:
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(url=url, ok=True, text="x" * 500)

    result = await check_official_url("https://example.com", FakeScraper())  # type: ignore[arg-type]

    assert result.ok is True
    assert result.text_chars == 500


def test_format_report_contains_gate_values() -> None:
    sample_item = sample("https://a.com", DiscoveryLabel.OFFICIAL_COMPANY)
    metrics = evaluate([sample_item], [check("https://a.com")])

    report = format_report(metrics)

    assert "crawl_worthy=1/1" in report
    assert "gates=" in report
