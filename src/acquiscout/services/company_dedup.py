"""Company-level identity and conservative deduplication."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import tldextract
from rapidfuzz import fuzz

from acquiscout.schemas import CompanyCandidate, Confidence, ResolvedEntity

LEGAL_SUFFIXES = (
    "llc",
    "l l c",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "lp",
    "llp",
    "pa",
    "pllc",
    "plc",
    "ltd",
)
CONFIDENCE_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


def root_domain(url: str | None) -> str | None:
    if not url:
        return None
    extracted = tldextract.extract(url)
    value = getattr(extracted, "top_domain_under_public_suffix", None)
    return value or extracted.registered_domain or extracted.domain or urlsplit(url).netloc or None


def normalize_name(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = [word for word in text.split() if word not in LEGAL_SUFFIXES]
    return " ".join(words)


def _confidence(value: Confidence) -> Confidence:
    return value


def _merge(existing: CompanyCandidate, entity: ResolvedEntity) -> CompanyCandidate:
    sources = list(existing.sources)
    seen = {(source.identifier_kind, source.identifier) for source in sources}
    for source in entity.sources:
        key = (source.identifier_kind, source.identifier)
        if key not in seen:
            seen.add(key)
            sources.append(source)

    registry_ids = list(dict.fromkeys([*existing.registry_ids, *entity.registry_ids]))
    flags = list(dict.fromkeys([*existing.review_flags, *entity.review_flags]))
    confidence = (
        entity.confidence
        if CONFIDENCE_RANK[entity.confidence] > CONFIDENCE_RANK[existing.confidence]
        else existing.confidence
    )
    official_url = existing.official_url or entity.official_url
    official_url_method = existing.official_url_method or (
        entity.method if entity.official_url else None
    )
    root = existing.root_domain or entity.root_domain
    name = existing.company_name
    if name == "Unknown company" and entity.company_name:
        name = entity.company_name
    profile = existing.registry_profile or entity.registry_profile
    return existing.model_copy(
        update={
            "company_name": name,
            "root_domain": root,
            "official_url": official_url,
            "official_url_method": official_url_method,
            "registry_ids": registry_ids,
            "registry_profile": profile,
            "sources": sources,
            "confidence": confidence,
            "review_flags": flags,
        }
    )


def _new_company(entity: ResolvedEntity) -> CompanyCandidate:
    if entity.registry_ids:
        key = entity.registry_ids[0]
    elif entity.root_domain:
        key = f"domain:{entity.root_domain}"
    else:
        key = f"name:{normalize_name(entity.company_name)}"
    return CompanyCandidate(
        company_key=key,
        company_name=entity.company_name,
        root_domain=entity.root_domain,
        official_url=entity.official_url,
        official_url_method=entity.method if entity.official_url else None,
        registry_ids=entity.registry_ids,
        registry_profile=entity.registry_profile,
        sources=entity.sources,
        confidence=entity.confidence,
        review_flags=entity.review_flags,
    )


def dedup_entities(entities: list[ResolvedEntity]) -> list[CompanyCandidate]:
    companies: list[CompanyCandidate] = []
    for entity in entities:
        normalized = normalize_name(entity.company_name)
        match: CompanyCandidate | None = None
        fuzzy_flags: list[str] = []

        for company in companies:
            if entity.registry_ids and set(entity.registry_ids) & set(company.registry_ids):
                match = company
                break
            if entity.registry_ids and company.registry_ids:
                continue
            if entity.root_domain and company.root_domain == entity.root_domain:
                match = company
                break
            if normalized and normalized == normalize_name(company.company_name):
                match = company
                break

        if match is None and normalized:
            for company in companies:
                if entity.registry_ids and company.registry_ids:
                    continue
                other = normalize_name(company.company_name)
                if not other:
                    continue
                if fuzz.token_sort_ratio(normalized, other) >= 97.0:
                    match = company
                    fuzzy_flags = ["fuzzy_company_merge"]
                    break

        if match is None:
            companies.append(_new_company(entity))
            continue

        if fuzzy_flags:
            entity = entity.model_copy(
                update={"review_flags": list(dict.fromkeys([*entity.review_flags, *fuzzy_flags]))}
            )
        index = companies.index(match)
        companies[index] = _merge(match, entity)

    return companies


__all__ = ["dedup_entities", "normalize_name", "root_domain"]
