from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from acquiscout.config import get_settings
from acquiscout.eval.discovery import (
    DiscoveryLabel,
    DiscoverySample,
    InputOutcome,
    check_official_url,
    evaluate,
    load_samples,
    root_domain,
    summarize_outcomes,
)
from acquiscout.schemas import Candidate, Confidence, RegistryProfile, ResolvedEntity
from acquiscout.services.company_dedup import dedup_entities
from acquiscout.services.company_url import CompanyUrlResolver, high_confidence_company
from acquiscout.services.relevance import RelevanceLabel, classify, clean_company_name
from acquiscout.services.url_judge import UrlJudge, UrlLabel
from acquiscout.sources.cache import SQLiteCache
from acquiscout.sources.directory import DirectoryResolver
from acquiscout.sources.govfiles import GovFilesSearchProvider
from acquiscout.sources.registry_evidence import RegistryEvidenceResolver
from acquiscout.sources.scrape import Scraper
from acquiscout.sources.tavily import TavilySearchProvider


def web_candidate(sample) -> Candidate:
    return Candidate(
        identifier=sample.url,
        identifier_kind="url",
        source_type="web",
        provider="eval",
        title=sample.title,
    )


def empty_profile(public_entity_url: str | None) -> RegistryProfile:
    return RegistryProfile(public_entity_url=public_entity_url)


async def run(samples_path: Path, output_dir: Path) -> int:
    settings = get_settings()
    samples = load_samples(samples_path)
    web = [s for s in samples if s.label is not DiscoveryLabel.REGISTRY]
    registry = [s for s in samples if s.label is DiscoveryLabel.REGISTRY]
    official = [s for s in web if s.label is DiscoveryLabel.OFFICIAL_COMPANY]
    directories = [s for s in web if s.label is DiscoveryLabel.DIRECTORY]
    input_domains = {root_domain(s.url) for s in web}

    cache = SQLiteCache(settings.search_cache_db_path, 3600)
    directory = DirectoryResolver(settings, cache=cache)
    govfiles = GovFilesSearchProvider(settings, cache=cache)
    scraper = Scraper(settings)
    evidence = RegistryEvidenceResolver(settings, scraper=scraper)
    tavily = TavilySearchProvider(settings, cache=cache)
    company_url = CompanyUrlResolver(tavily)

    entities: list[ResolvedEntity] = []
    dropped: list[dict] = []
    directory_resolved_urls = 0
    directory_resolved_names = 0
    directory_non_target: set[str] = set()

    for sample in official:
        candidate = web_candidate(sample)
        url = str(candidate.identifier)
        entities.append(
            ResolvedEntity(
                company_name=clean_company_name(sample.title, url),
                official_url=url,
                root_domain=root_domain(url),
                method="web",
                confidence=Confidence.MEDIUM,
                sources=[candidate],
            )
        )

    for sample in directories:
        candidate = web_candidate(sample)
        resolved = await directory.resolve(candidate)
        if resolved is None or not resolved.company_name:
            dropped.append({"url": sample.url, "reason": "directory_unresolved"})
            continue
        directory_resolved_names += 1
        if resolved.official_url:
            directory_resolved_urls += 1
            if (
                classify(
                    Candidate(
                        identifier=resolved.official_url,
                        identifier_kind="url",
                        source_type="web",
                        provider="directory",
                        title=resolved.company_name,
                    )
                )
                is RelevanceLabel.COMPANY
            ):
                entities.append(resolved)
                continue
            directory_non_target.add(sample.url)
            dropped.append({"url": sample.url, "reason": "resolved_non_target"})
            continue
        entities.append(resolved)

    registry_profiles = 0
    registry_public = 0
    registry_officer2 = 0
    registry_active = 0
    registry_by_source: Counter[str] = Counter()
    registry_public_by_source: Counter[str] = Counter()
    url_search_eligible = 0
    url_search_resolved = 0
    registry_url_resolved = 0
    directory_url_search_resolved = 0

    for sample in registry:
        candidate = Candidate(
            identifier=sample.registry_number or sample.url,
            identifier_kind="registry_id",
            registry_jurisdiction=sample.registry_jurisdiction,
            source_type="registry",
            provider="govfiles_registry",
            title=sample.company_name or sample.title,
            public_entity_url=sample.url,
        )
        registry_id = f"{sample.registry_jurisdiction}:{sample.registry_number}"
        try:
            profile = await govfiles.get_company(
                sample.registry_jurisdiction or "us_fl",
                sample.registry_number or "",
            )
        except Exception:
            profile = empty_profile(sample.url)
        registry_profiles += 1
        evidence_row = await evidence.resolve(
            profile.public_entity_url, sample.registry_number or ""
        )
        update = {
            "public_detail_url": evidence_row.public_detail_url,
            "evidence_status": evidence_row.evidence_status,
            "evidence_reason": evidence_row.reason,
            "evidence_source": evidence_row.source,
            "evidence_http_status": evidence_row.http_status,
            "evidence_backend": evidence_row.backend,
            "evidence_checked_at": evidence_row.fetched_at,
        }
        for field in (
            "status",
            "legal_form",
            "formed_on",
            "registered_address",
            "license_number",
            "license_type",
            "license_status",
            "license_expiration",
            "licensee_name",
        ):
            value = getattr(evidence_row, field)
            if value is not None:
                update[field] = value
        if evidence_row.officers:
            update["officers"] = evidence_row.officers
        profile = profile.model_copy(update=update)
        registry_by_source[profile.evidence_source or "unknown"] += 1
        if profile.evidence_status == "public_detail":
            registry_public += 1
            registry_public_by_source[profile.evidence_source or "unknown"] += 1
        if len(profile.officers) >= 2:
            registry_officer2 += 1
        if (profile.status or "").lower() == "active":
            registry_active += 1
        entities.append(
            ResolvedEntity(
                company_name=sample.company_name or sample.title or sample.url,
                official_url=None,
                root_domain=None,
                method="registry",
                confidence=Confidence.MEDIUM,
                registry_ids=[registry_id],
                registry_profile=profile,
                sources=[candidate],
            )
        )

    companies = dedup_entities(entities)
    url_searches = 0
    for index, company in enumerate(companies):
        if company.official_url is None and high_confidence_company(company, allow_directory=True):
            url_search_eligible += 1
            result = await company_url.resolve(company.company_name)
            url_searches += 1
            if result.official_url:
                url_search_resolved += 1
                if company.registry_ids:
                    registry_url_resolved += 1
                else:
                    directory_url_search_resolved += 1
                companies[index] = company.model_copy(
                    update={
                        "official_url": result.official_url,
                        "root_domain": root_domain(result.official_url),
                        "official_url_method": "search",
                        "confidence": result.confidence,
                        "review_flags": list(
                            dict.fromkeys([*company.review_flags, *result.review_flags])
                        ),
                    }
                )
            else:
                companies[index] = company.model_copy(
                    update={
                        "review_flags": list(
                            dict.fromkeys([*company.review_flags, *result.review_flags])
                        )
                    }
                )

    kept = []
    for company in companies:
        profile = company.registry_profile
        if company.official_url is not None:
            kept.append(company)
        elif profile is not None and profile.evidence_status == "public_detail":
            kept.append(company)
        else:
            dropped.append(
                {
                    "company_key": company.company_key,
                    "company_name": company.company_name,
                    "reason": (
                        "missing_public_registry_evidence"
                        if profile is not None
                        else "missing_official_url"
                    ),
                }
            )

    source_to_company = {
        (source.identifier_kind, source.identifier): company
        for company in kept
        for source in company.sources
    }
    input_outcomes: list[dict[str, object]] = []
    converted_non_target = 0
    for sample in web:
        company = source_to_company.get(("url", sample.url))
        if sample.label is DiscoveryLabel.OFFICIAL_COMPANY:
            outcome = (
                InputOutcome.OFFICIAL
                if company is not None and company.official_url
                else InputOutcome.UNRESOLVED
            )
        elif sample.label is DiscoveryLabel.DIRECTORY:
            if company is not None and company.official_url:
                outcome = InputOutcome.OFFICIAL
                converted_non_target += 1
            elif sample.url in directory_non_target:
                outcome = InputOutcome.NON_TARGET
            else:
                outcome = InputOutcome.UNRESOLVED
        else:
            outcome = InputOutcome.NON_TARGET
        input_outcomes.append(
            {
                "input_url": sample.url,
                "input_label": sample.label.value,
                "outcome": outcome.value,
                "company_key": company.company_key if company else None,
                "official_url": company.official_url if company else None,
            }
        )
    fixed = summarize_outcomes([InputOutcome(item["outcome"]) for item in input_outcomes])
    baseline_non_target = len(web) - len(official)
    fixed["converted_non_target"] = converted_non_target
    fixed["baseline_non_target"] = baseline_non_target
    fixed["non_target_conversion"] = (
        round(converted_non_target / baseline_non_target, 4) if baseline_non_target else 0
    )
    baseline_official_domains = len({root_domain(s.url) for s in official})
    fixed["baseline_official_diversity"] = (
        round(baseline_official_domains / len(official), 4) if official else 0
    )

    await directory.aclose()
    await govfiles.aclose()
    await evidence.aclose()
    await tavily.aclose()

    web_urls = [company.official_url for company in kept if company.official_url]
    web_company_urls = [
        url
        for url in web_urls
        if classify(
            Candidate(
                identifier=url,
                identifier_kind="url",
                source_type="web",
                provider="company",
            )
        )
        is RelevanceLabel.COMPANY
    ]
    structured = [company for company in kept if company.registry_ids and not company.official_url]
    output_domains = {root_domain(url) for url in web_urls}

    original_official = {sample.url.rstrip("/") for sample in official}
    judge = UrlJudge(settings, cache=cache)
    final_samples: list[DiscoverySample] = []
    final_checks = []
    kept_companies = []
    judge_failures = 0
    for company in kept:
        origin = (
            "original_web"
            if company.official_url and company.official_url.rstrip("/") in original_official
            else "registry_search"
            if company.registry_ids
            else "directory_resolved"
            if company.official_url
            else "structured_registry"
        )
        kept_companies.append(
            {
                "company_key": company.company_key,
                "company_name": company.company_name,
                "official_url": company.official_url,
                "official_url_method": company.official_url_method,
                "registry_ids": company.registry_ids,
                "evidence_status": (
                    company.registry_profile.evidence_status if company.registry_profile else None
                ),
                "evidence_reason": (
                    company.registry_profile.evidence_reason if company.registry_profile else None
                ),
                "evidence_source": (
                    company.registry_profile.evidence_source if company.registry_profile else None
                ),
                "review_flags": company.review_flags,
                "url_origin": origin,
            }
        )
        if not company.official_url:
            continue
        if company.official_url.rstrip("/") in original_official:
            label = DiscoveryLabel.OFFICIAL_COMPANY
        else:
            try:
                judgement = await judge.judge(company.official_url, company.company_name)
                label = {
                    UrlLabel.OFFICIAL_COMPANY: DiscoveryLabel.OFFICIAL_COMPANY,
                    UrlLabel.DIRECTORY: DiscoveryLabel.DIRECTORY,
                    UrlLabel.GOVERNMENT: DiscoveryLabel.GOVERNMENT,
                    UrlLabel.NEWS: DiscoveryLabel.NEWS,
                    UrlLabel.OTHER: DiscoveryLabel.OTHER,
                }[judgement.label]
            except Exception:
                judge_failures += 1
                label = DiscoveryLabel.OTHER
        check = await check_official_url(
            company.official_url,
            scraper,
            min_chars=settings.crawl_min_text_chars,
        )
        final_checks.append(check)
        final_samples.append(
            DiscoverySample(
                url=company.official_url,
                title=company.company_name,
                label=label,
            )
        )
    await scraper.aclose()
    await judge.aclose()
    final_metrics = evaluate(final_samples, final_checks) if final_samples else None

    metrics = {
        "sample_size": len(samples),
        "input_web": len(web),
        "input_official": len(official),
        "input_directory": len(directories),
        "input_non_target": len(web) - len(official),
        "input_crawl_worthy_precision": round(len(official) / len(web), 4) if web else 0,
        "input_non_target_share": round((len(web) - len(official)) / len(web), 4) if web else 0,
        "input_domain_diversity": round(len(input_domains) / len(web), 4) if web else 0,
        "resolver_name_rate": round(directory_resolved_names / len(directories), 4)
        if directories
        else 0,
        "resolver_url_rate": round(directory_resolved_urls / len(directories), 4)
        if directories
        else 0,
        "resolver_dropped": len([d for d in dropped if "url" in d]),
        "registry_count": len(registry),
        "registry_profiles": registry_profiles,
        "registry_public_detail": registry_public,
        "registry_officer2": registry_officer2,
        "registry_active": registry_active,
        "registry_by_source": dict(sorted(registry_by_source.items())),
        "registry_public_by_source": dict(sorted(registry_public_by_source.items())),
        "url_search_eligible": url_search_eligible,
        "url_search_resolved": url_search_resolved,
        "registry_url_resolved": registry_url_resolved,
        "directory_url_search_resolved": directory_url_search_resolved,
        "company_url_searches": url_searches,
        "company_count": len(kept),
        "company_web_with_url": len(web_urls),
        "company_structured_registry": len(structured),
        "company_dropped": len(dropped),
        "company_actionable_rate": round(len(web_urls) / len(kept), 4) if kept else 0,
        "output_domain_diversity": round(len(output_domains) / len(web_urls), 4) if web_urls else 0,
        "post_web_urls": len(web_urls),
        "post_non_target_urls": len(web_urls) - len(web_company_urls),
        "post_coverage": round(len(web_urls) / len(web), 4) if web else 0,
        "final_coverage": round(len(final_samples) / len(web), 4) if web else 0,
        "judge_failures": judge_failures,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = output_dir / f"resolver_eval_{stamp}.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": "resolver_eval_v1",
                "evaluated_at": datetime.now(UTC).isoformat(),
                "samples_file": str(samples_path),
                "metrics": metrics,
                "final_metrics": (final_metrics.model_dump(mode="json") if final_metrics else None),
                "fixed_metrics": fixed,
                "input_outcomes": input_outcomes,
                "kept_companies": kept_companies,
                "final_checks": [check.model_dump(mode="json") for check in final_checks],
                "dropped": dropped,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps(metrics, indent=2))
    print("FIXED")
    print(json.dumps(fixed, indent=2))
    if final_metrics is not None:
        print("FINAL")
        print(json.dumps(final_metrics.model_dump(mode="json"), indent=2))
    print(f"wrote={target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the baseline batch through Phase 3")
    parser.add_argument("--samples", type=Path, default=Path("data/raw/discovery_eval_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("data/generated/discovery_eval"))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(run(args.samples, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
