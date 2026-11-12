from __future__ import annotations

import pytest

from acquiscout.schemas import CompanyCandidate, RegistryProfile, SearchTask
from acquiscout.services.company_url import CompanyUrlResolver, high_confidence_company


class FakeTavily:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results

    async def search(self, task: SearchTask) -> list[dict[str, object]]:
        return self.results


def company(name: str = "Acme Title Company") -> CompanyCandidate:
    return CompanyCandidate(
        company_key="us_fl:H30572",
        company_name=name,
        registry_ids=["us_fl:H30572"],
        registry_profile=RegistryProfile(
            status="active",
            evidence_status="public_detail",
            public_detail_url="https://search.sunbiz.org/example",
        ),
    )


def test_high_confidence_requires_public_active_sector_match() -> None:
    assert high_confidence_company(company()) is True
    assert high_confidence_company(company("Hillsborough County Sheriff's Office")) is False
    assert high_confidence_company(company("Acme Holdings")) is False


def test_directory_name_can_be_high_confidence_when_allowed() -> None:
    directory_company = CompanyCandidate(
        company_key="name:acme title company",
        company_name="Acme Title Company",
    )

    assert high_confidence_company(directory_company) is False
    assert high_confidence_company(directory_company, allow_directory=True) is True


@pytest.mark.asyncio
async def test_resolver_picks_official_company_site() -> None:
    resolver = CompanyUrlResolver(
        FakeTavily(
            [
                {"url": "https://leadiq.com/c/acme", "title": "Acme Title Company | LeadIQ"},
                {"url": "https://acmetitle.com", "title": "Acme Title Company"},
                {"url": "https://pinellas.gov/department/x", "title": "Acme Title Company"},
            ]
        )
    )  # type: ignore[arg-type]

    result = await resolver.resolve("Acme Title Company")

    assert result.official_url == "https://acmetitle.com"
    assert result.confidence.value == "medium"


@pytest.mark.asyncio
async def test_resolver_rejects_unrelated_or_ambiguous_results() -> None:
    resolver = CompanyUrlResolver(
        FakeTavily([{"url": "https://other.com", "title": "Other Company"}])
    )  # type: ignore[arg-type]

    result = await resolver.resolve("Acme Title Company")

    assert result.official_url is None
    assert "no_search_match" in result.review_flags
