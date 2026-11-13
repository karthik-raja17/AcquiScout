from __future__ import annotations

import pytest

from acquiscout.config import Settings
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.scrape import ScrapeResult

SEARCH_MARKDOWN = (
    "Markdown Content:\n"
    "| [HILLSBOROUGH TITLE, INC.]("
    "https://search.sunbiz.org/Inquiry/CorporationSearch/"
    'SearchResultDetail?aggregateId=domp-h30572-123 "Go to Detail Screen") | H30572 | Active |\n'
)

DBPR_MARKDOWN = """
Markdown Content:
License Number: CGC123456
License Type: Certified General Contractor
License Status: Active
Expiration Date: 08/31/2027
Licensee Name: ACME PROPERTY MANAGEMENT LLC
Business Address:
123 MAIN STREET
TAMPA, FL 33601
"""

DFS_MARKDOWN = """
Markdown Content:
License Number: W123456
License Type: Title Agency
License Status: Active
Expiration Date: 09/30/2027
Licensee Name: ACME TITLE AGENCY LLC
Business Address:
456 CENTRAL AVENUE
ST PETERSBURG, FL 33701
"""

DETAIL_MARKDOWN = """
Markdown Content:
Florida Profit Corporation

HILLSBOROUGH TITLE, INC.

Filing Information
Document Number H30572 Date Filed 11/20/1984 State FL Status ACTIVE

Principal Address
1605 SOUTH ALEXANDER STREET
PLANT CITY, FL 33563

Officer/Director Detail
Title President
Gregory, Amy L
1605 S ALEXANDER STREET

Title Secretary
Reed, Dana
1605 S ALEXANDER STREET

Annual Reports
2026 04/21/2026
"""


def make_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


class FakeScraper:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def fetch(self, url: str) -> ScrapeResult:
        self.urls.append(url)
        text = SEARCH_MARKDOWN if "SearchResults" in url else DETAIL_MARKDOWN
        return ScrapeResult(url=url, ok=True, text=text)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_sunbiz_evidence_parses_public_detail() -> None:
    scraper = FakeScraper()
    resolver = RegistryEvidenceResolver(make_settings(), scraper=scraper)  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults/EntityName/x/Page1",
        "H30572",
    )

    assert evidence is not None
    assert evidence.evidence_status == "public_detail"
    assert evidence.public_detail_url is not None
    assert "SearchResultDetail" in evidence.public_detail_url
    assert evidence.legal_form == "Florida Profit Corporation"
    assert evidence.status == "ACTIVE"
    assert evidence.formed_on is not None
    assert evidence.formed_on.year == 1984
    assert [officer.name for officer in evidence.officers] == [
        "Gregory, Amy L",
        "Reed, Dana",
    ]
    assert len(scraper.urls) == 2


@pytest.mark.asyncio
async def test_sunbiz_without_detail_link_is_partial() -> None:
    class NoLinkScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(url=url, ok=True, text="Markdown Content:\nNo detail link\n")

    resolver = RegistryEvidenceResolver(make_settings(), scraper=NoLinkScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults/EntityName/x/Page1",
        "H30572",
    )

    assert evidence is not None
    assert evidence.evidence_status == "search_page_only"
    assert evidence.reason == "no_detail_link"
    assert evidence.source == "sunbiz"
    assert evidence.public_detail_url is None


@pytest.mark.asyncio
async def test_unapproved_evidence_url_is_rejected() -> None:
    resolver = RegistryEvidenceResolver(make_settings(), scraper=FakeScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve("https://example.com/company", "H30572")

    assert evidence.evidence_status == "unsupported_source"
    assert evidence.reason == "unapproved_registry_url"


@pytest.mark.asyncio
async def test_rate_limited_search_page_records_reason() -> None:
    class RateLimitedScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(
                url=url,
                ok=False,
                backend="jina",
                status="rate_limited",
                status_code=429,
                reason="http_429",
                retry_after_seconds=12.0,
                circuit_failure=True,
            )

    resolver = RegistryEvidenceResolver(make_settings(), scraper=RateLimitedScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults/EntityName/x/Page1",
        "H30572",
    )

    assert evidence.evidence_status == "rate_limited"
    assert evidence.reason == "http_429"
    assert evidence.http_status == 429
    assert evidence.source == "sunbiz"


@pytest.mark.asyncio
async def test_blocked_search_page_records_cloudflare_reason() -> None:
    class BlockedScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(
                url=url,
                ok=False,
                backend="jina",
                status="blocked",
                status_code=200,
                target_status=403,
                title="Just a moment...",
                reason="cloudflare_challenge",
                text="Title: Just a moment...",
                circuit_failure=True,
            )

    resolver = RegistryEvidenceResolver(make_settings(), scraper=BlockedScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults/EntityName/x/Page1",
        "H30572",
    )

    assert evidence.evidence_status == "blocked"
    assert evidence.reason == "cloudflare_challenge"
    assert evidence.source == "sunbiz"
    assert evidence.http_status == 403
    assert evidence.public_detail_url is None


@pytest.mark.asyncio
async def test_dbpr_license_page_is_parsed() -> None:
    class DBPRScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(url=url, ok=True, text=DBPR_MARKDOWN)

    resolver = RegistryEvidenceResolver(make_settings(), scraper=DBPRScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://www.myfloridalicense.com/CheckListDetail.asp?SID=123",
        "",
    )

    assert evidence.evidence_status == "public_detail"
    assert evidence.source == "dbpr"
    assert evidence.public_detail_url == (
        "https://www.myfloridalicense.com/CheckListDetail.asp?SID=123"
    )
    assert evidence.license_number == "CGC123456"
    assert evidence.license_type == "Certified General Contractor"
    assert evidence.license_status == "Active"
    assert evidence.license_expiration is not None
    assert evidence.license_expiration.year == 2027
    assert evidence.licensee_name == "ACME PROPERTY MANAGEMENT LLC"


@pytest.mark.asyncio
async def test_dfs_license_page_is_parsed() -> None:
    class DFSScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(url=url, ok=True, text=DFS_MARKDOWN)

    resolver = RegistryEvidenceResolver(make_settings(), scraper=DFSScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://www.dos.fl.gov/license/123",
        "",
    )

    assert evidence.evidence_status == "public_detail"
    assert evidence.source == "dfs"
    assert evidence.license_number == "W123456"
    assert evidence.license_type == "Title Agency"
    assert evidence.license_status == "Active"
    assert evidence.licensee_name == "ACME TITLE AGENCY LLC"


@pytest.mark.asyncio
async def test_license_page_without_license_fields_is_not_evidence() -> None:
    class NameOnlyScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(
                url=url,
                ok=True,
                text="Markdown Content:\nName: ACME PROPERTY MANAGEMENT LLC\n",
            )

    resolver = RegistryEvidenceResolver(make_settings(), scraper=NameOnlyScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://www.myfloridalicense.com/CheckListDetail.asp?SID=123",
        "",
    )

    assert evidence.evidence_status == "parse_failed"
    assert evidence.reason == "no_license_fields"


@pytest.mark.asyncio
async def test_dbpr_search_page_without_license_fields_is_partial() -> None:
    class DBPRSearchScraper(FakeScraper):
        async def fetch(self, url: str) -> ScrapeResult:
            return ScrapeResult(
                url=url,
                ok=True,
                text="Markdown Content:\nLicense Search\nNo records found\n",
            )

    resolver = RegistryEvidenceResolver(make_settings(), scraper=DBPRSearchScraper())  # type: ignore[arg-type]

    evidence = await resolver.resolve(
        "https://www.myfloridalicense.com/wl11.asp?mode=2",
        "",
    )

    assert evidence.evidence_status == "search_page_only"
    assert evidence.reason == "no_license_detail"
    assert evidence.source == "dbpr"
