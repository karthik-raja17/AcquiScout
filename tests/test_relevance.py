from __future__ import annotations

from acquiscout.schemas import Candidate
from acquiscout.services.relevance import (
    RelevanceLabel,
    classify,
    clean_company_name,
)


def candidate(url: str, kind: str = "url", title: str | None = None) -> Candidate:
    return Candidate(
        identifier=url,
        identifier_kind=kind,  # type: ignore[arg-type]
        source_type="registry" if kind == "registry_id" else "web",
        provider="govfiles_registry" if kind == "registry_id" else "tavily_web",
        title=title,
    )


def test_classify_known_hosts() -> None:
    assert classify(candidate("https://leadiq.com/c/acme")) is RelevanceLabel.DIRECTORY
    assert classify(candidate("https://www.mapquest.com/us/fl/acme")) is RelevanceLabel.DIRECTORY
    assert classify(candidate("https://pinellas.gov/department/x")) is RelevanceLabel.GOVERNMENT
    assert classify(candidate("https://joinhcso.com/")) is RelevanceLabel.GOVERNMENT
    assert classify(candidate("https://tampabay.com/news")) is RelevanceLabel.NEWS
    assert classify(candidate("https://hillsboroughtitle.com/")) is RelevanceLabel.COMPANY
    assert classify(candidate("P123", kind="registry_id")) is RelevanceLabel.REGISTRY


def test_clean_company_name_strips_directory_suffixes() -> None:
    assert (
        clean_company_name("Hillsborough Title | LeadIQ", "https://leadiq.com/c/x")
        == "Hillsborough Title"
    )
    assert (
        clean_company_name("Home - Tampa Title Company, FL", "https://tampatitle.com")
        == "Tampa Title Company"
    )
    assert (
        clean_company_name("Pinellas Property Managers | Champa Tampa", "https://champatampa.com")
        == "Pinellas Property Managers"
    )


def test_clean_company_name_falls_back_to_host() -> None:
    assert clean_company_name(None, "https://acme-title-company.com") == "Acme Title Company"
