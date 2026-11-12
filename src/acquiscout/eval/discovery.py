"""Discovery crawl-worthiness evaluation.

Discovery is judged on whether it hands the crawl stage fetchable, company-like
URLs. Golden-set recall is intentionally not a discovery metric.
"""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import tldextract
import trafilatura
from pydantic import BaseModel, Field

from acquiscout.config import Settings
from acquiscout.sources.scrape import Scraper

BLOCK_MARKERS = ("captcha", "enable javascript", "access denied", "are you a robot")


class DiscoveryLabel(StrEnum):
    OFFICIAL_COMPANY = "official_company"
    DIRECTORY = "directory"
    GOVERNMENT = "government"
    NEWS = "news"
    OTHER = "other"
    REGISTRY = "registry"
    UNFETCHABLE = "unfetchable"


class InputOutcome(StrEnum):
    OFFICIAL = "official"
    NON_TARGET = "non_target"
    UNRESOLVED = "unresolved"


def summarize_outcomes(outcomes: list[InputOutcome]) -> dict[str, int | float]:
    total = len(outcomes)
    official = sum(1 for item in outcomes if item is InputOutcome.OFFICIAL)
    non_target = sum(1 for item in outcomes if item is InputOutcome.NON_TARGET)
    unresolved = sum(1 for item in outcomes if item is InputOutcome.UNRESOLVED)
    denominator = total or 1
    return {
        "input_web": total,
        "official": official,
        "non_target": non_target,
        "unresolved": unresolved,
        "usable_rate": round(official / denominator, 4),
        "non_target_rate": round(non_target / denominator, 4),
        "unresolved_rate": round(unresolved / denominator, 4),
    }


class DiscoverySample(BaseModel):
    url: str
    title: str | None = None
    label: DiscoveryLabel
    company_name: str | None = None
    registry_jurisdiction: str | None = None
    registry_number: str | None = None
    notes: str | None = None


class FetchCheck(BaseModel):
    url: str
    ok: bool
    status_code: int | None = None
    content_type: str | None = None
    text_chars: int = 0
    reason: str | None = None


class DiscoveryMetrics(BaseModel):
    sample_size: int
    web_sample_size: int
    registry_count: int
    official_company: int
    official_fetch_ok: int
    official_fetch_success_rate: float
    crawl_worthy_precision: float
    non_target_share: float
    unfetchable_count: int
    unique_root_domains: int
    domain_diversity: float
    duplicate_rate: float
    gates: dict[str, bool] = Field(default_factory=dict)


def load_samples(path: str | Path) -> list[DiscoverySample]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("samples", []) if isinstance(data, dict) else data
    return [DiscoverySample.model_validate(item) for item in raw]


def root_domain(url: str) -> str:
    extracted = tldextract.extract(url)
    root = getattr(extracted, "top_domain_under_public_suffix", None)
    return root or extracted.registered_domain or extracted.domain or urlsplit(url).netloc


async def check_url(
    url: str,
    *,
    min_chars: int = 400,
    client: httpx.AsyncClient | None = None,
) -> FetchCheck:
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        headers={"User-Agent": "AcquiScout/0.1 (+approved-source research)"},
    )
    try:
        response = await http.get(url)
        if response.status_code < 200 or response.status_code >= 300:
            return FetchCheck(
                url=url,
                ok=False,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                reason=f"http_{response.status_code}",
            )
        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            return FetchCheck(
                url=url,
                ok=False,
                status_code=response.status_code,
                content_type=content_type,
                reason="not_html",
            )
        text = (
            trafilatura.extract(
                response.text,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            or ""
        )
        lowered = text.lower()
        if any(marker in lowered for marker in BLOCK_MARKERS):
            return FetchCheck(
                url=url,
                ok=False,
                status_code=response.status_code,
                content_type=content_type,
                text_chars=len(text.strip()),
                reason="blocked_marker",
            )
        if len(text.strip()) < min_chars:
            return FetchCheck(
                url=url,
                ok=False,
                status_code=response.status_code,
                content_type=content_type,
                text_chars=len(text.strip()),
                reason="too_little_text",
            )
        return FetchCheck(
            url=url,
            ok=True,
            status_code=response.status_code,
            content_type=content_type,
            text_chars=len(text.strip()),
        )
    except Exception as exc:
        return FetchCheck(url=url, ok=False, reason=f"{type(exc).__name__}: {exc}")
    finally:
        if own_client:
            await http.aclose()


def evaluate(samples: list[DiscoverySample], checks: list[FetchCheck]) -> DiscoveryMetrics:
    by_url = {check.url: check for check in checks}
    web = [sample for sample in samples if sample.label is not DiscoveryLabel.REGISTRY]
    registry_count = len(samples) - len(web)
    total = len(web)

    official_samples = [sample for sample in web if sample.label is DiscoveryLabel.OFFICIAL_COMPANY]
    official = len(official_samples)
    official_fetch_ok = sum(
        1
        for sample in official_samples
        if by_url.get(sample.url, FetchCheck(url=sample.url, ok=False)).ok
    )
    non_target = sum(
        1
        for sample in web
        if sample.label
        in {
            DiscoveryLabel.DIRECTORY,
            DiscoveryLabel.GOVERNMENT,
            DiscoveryLabel.NEWS,
            DiscoveryLabel.OTHER,
        }
    )
    unfetchable = sum(1 for sample in web if sample.label is DiscoveryLabel.UNFETCHABLE)
    domains = {root_domain(sample.url) for sample in web}

    denominator = total or 1
    official_fetch_success_rate = official_fetch_ok / (official or 1)
    crawl_worthy_precision = official / denominator
    non_target_share = non_target / denominator
    domain_diversity = len(domains) / denominator
    duplicate_rate = 1.0 - domain_diversity

    return DiscoveryMetrics(
        sample_size=len(samples),
        web_sample_size=total,
        registry_count=registry_count,
        official_company=official,
        official_fetch_ok=official_fetch_ok,
        official_fetch_success_rate=round(official_fetch_success_rate, 4),
        crawl_worthy_precision=round(crawl_worthy_precision, 4),
        non_target_share=round(non_target_share, 4),
        unfetchable_count=unfetchable,
        unique_root_domains=len(domains),
        domain_diversity=round(domain_diversity, 4),
        duplicate_rate=round(duplicate_rate, 4),
        gates={
            "fetch_success": official_fetch_success_rate >= 0.90,
            "crawl_worthy": crawl_worthy_precision >= 0.80,
        },
    )


async def check_official_url(
    url: str,
    scraper: Scraper,
    *,
    min_chars: int = 400,
) -> FetchCheck:
    result = await scraper.fetch(url)
    text_chars = len(result.text.strip())
    ok = result.ok and text_chars >= min_chars
    reason = result.reason or (None if ok else "too_little_text")
    return FetchCheck(url=url, ok=ok, text_chars=text_chars, reason=reason)


async def evaluate_samples(
    samples: list[DiscoverySample],
    settings: Settings,
    *,
    scraper: Scraper | None = None,
    min_chars: int = 400,
    concurrency: int = 2,
) -> tuple[DiscoveryMetrics, list[FetchCheck]]:
    owned = scraper is None
    active_scraper = scraper or Scraper(settings)
    semaphore = asyncio.Semaphore(concurrency)

    async def limited(sample: DiscoverySample) -> FetchCheck:
        async with semaphore:
            return await check_official_url(sample.url, active_scraper, min_chars=min_chars)

    try:
        checks = await asyncio.gather(
            *(
                limited(sample)
                for sample in samples
                if sample.label is DiscoveryLabel.OFFICIAL_COMPANY
            )
        )
    finally:
        if owned:
            await active_scraper.aclose()
    return evaluate(samples, list(checks)), list(checks)


def format_report(metrics: DiscoveryMetrics) -> str:
    return "\n".join(
        [
            f"samples={metrics.sample_size} web={metrics.web_sample_size} "
            f"registry={metrics.registry_count}",
            f"official_fetch={metrics.official_fetch_ok}/{metrics.official_company} "
            f"({metrics.official_fetch_success_rate:.1%})",
            f"crawl_worthy={metrics.official_company}/{metrics.web_sample_size} "
            f"({metrics.crawl_worthy_precision:.1%})",
            f"non_target_share={metrics.non_target_share:.1%}",
            f"unique_root_domains={metrics.unique_root_domains} ({metrics.domain_diversity:.1%})",
            f"gates={metrics.gates}",
        ]
    )


__all__ = [
    "DiscoveryLabel",
    "DiscoveryMetrics",
    "DiscoverySample",
    "FetchCheck",
    "InputOutcome",
    "check_official_url",
    "check_url",
    "evaluate",
    "evaluate_samples",
    "format_report",
    "load_samples",
    "root_domain",
    "summarize_outcomes",
]
