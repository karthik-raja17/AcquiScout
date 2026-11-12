from __future__ import annotations

from acquiscout.schemas import Candidate, Confidence, ResolvedEntity
from acquiscout.services.company_dedup import dedup_entities, normalize_name, root_domain


def source(url: str, kind: str = "url") -> Candidate:
    return Candidate(
        identifier=url,
        identifier_kind=kind,  # type: ignore[arg-type]
        source_type="registry" if kind == "registry_id" else "web",
        provider="govfiles_registry" if kind == "registry_id" else "tavily_web",
    )


def entity(
    name: str,
    url: str | None = None,
    *,
    registry_ids: list[str] | None = None,
    confidence: Confidence = Confidence.MEDIUM,
    flags: list[str] | None = None,
) -> ResolvedEntity:
    identifier = url or (registry_ids or ["unknown"])[0]
    return ResolvedEntity(
        company_name=name,
        official_url=url,
        root_domain=root_domain(url),
        method="web" if url else "registry",
        confidence=confidence,
        registry_ids=registry_ids or [],
        sources=[source(identifier, "url" if url else "registry_id")],
        review_flags=flags or [],
    )


def test_normalize_name_removes_legal_suffixes() -> None:
    assert normalize_name("Hillsborough Title, Inc.") == "hillsborough title"
    assert normalize_name("HILLSBOROUGH TITLE, LLC") == "hillsborough title"
    assert normalize_name("Acme Property Management Company") == "acme property management"


def test_exact_domain_merge() -> None:
    companies = dedup_entities(
        [
            entity("Hillsborough Title", "https://hillsboroughtitle.com/"),
            entity("Hillsborough Title About", "https://www.hillsboroughtitle.com/about"),
        ]
    )

    assert len(companies) == 1
    assert companies[0].root_domain == "hillsboroughtitle.com"
    assert len(companies[0].sources) == 2


def test_registry_and_web_merge_by_normalized_name() -> None:
    companies = dedup_entities(
        [
            entity("HILLSBOROUGH TITLE, INC.", registry_ids=["us_fl:H30572"]),
            entity("Hillsborough Title", "https://hillsboroughtitle.com/"),
        ]
    )

    assert len(companies) == 1
    assert companies[0].registry_ids == ["us_fl:H30572"]
    assert companies[0].official_url == "https://hillsboroughtitle.com/"


def test_distinct_registry_entities_do_not_merge_by_name() -> None:
    companies = dedup_entities(
        [
            entity("PINELLAS PROPERTY MANAGEMENT, INC.", registry_ids=["us_fl:1"]),
            entity("PINELLAS PROPERTY MANAGEMENT, LLC", registry_ids=["us_fl:2"]),
        ]
    )

    assert len(companies) == 2


def test_distinct_names_do_not_merge() -> None:
    companies = dedup_entities(
        [
            entity("Hillsborough Title", "https://hillsboroughtitle.com/"),
            entity("Hillsborough County Title Company", "https://hillsboroughcountytitle.com/"),
        ]
    )

    assert len(companies) == 2


def test_root_domain_uses_registrable_domain() -> None:
    assert root_domain("https://www.example.co.uk/path") == "example.co.uk"
