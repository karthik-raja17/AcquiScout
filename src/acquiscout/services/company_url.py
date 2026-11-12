"""Experimental high-confidence company-name to official-website resolution.

Only wired when ``enable_directory_resolver`` is true; see
``docs/resolver-experiment.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from acquiscout.schemas import Candidate, CompanyCandidate, Confidence, SearchTask
from acquiscout.services.company_dedup import normalize_name
from acquiscout.services.relevance import RelevanceLabel, classify
from acquiscout.sources.policy import SourcePolicy, is_approved_registry_url
from acquiscout.sources.providers import SearchProvider

GOVERNMENT_WORDS = {
    "county",
    "sheriff",
    "government",
    "city of",
    "school",
    "tax collector",
}
SECTOR_WORDS = {
    "title",
    "escrow",
    "abstract",
    "property management",
    "hoa",
    "community association",
    "realty",
}


@dataclass
class CompanyUrlResult:
    official_url: str | None = None
    confidence: Confidence = Confidence.LOW
    review_flags: list[str] = field(default_factory=list)


def high_confidence_company(company: CompanyCandidate, *, allow_directory: bool = False) -> bool:
    name = company.company_name.lower()
    if any(word in name for word in GOVERNMENT_WORDS):
        return False
    if not any(word in name for word in SECTOR_WORDS):
        return False
    if len([token for token in normalize_name(company.company_name).split() if token]) < 2:
        return False
    profile = company.registry_profile
    if profile is None:
        return bool(allow_directory and not company.registry_ids)
    if profile.evidence_status != "public_detail":
        return False
    if profile.status and profile.status.lower() not in {"active", "act"}:
        return False
    return True


class CompanyUrlResolver:
    def __init__(self, tavily: SearchProvider, policy: SourcePolicy | None = None) -> None:
        self._tavily = tavily
        self._policy = policy or SourcePolicy()

    async def resolve(self, company_name: str) -> CompanyUrlResult:
        query = f'"{company_name}" Tampa Bay Florida official website'
        task = SearchTask(
            task_id="company-url",
            node="tavily_web",
            query=query,
            params={"max_results": 5},
            search_plan_index=0,
        )
        results = await self._tavily.search(task)
        best: tuple[float, str] | None = None
        second_best = 0.0
        for result in results:
            url = result.get("url")
            title = result.get("title") or ""
            if not isinstance(url, str) or not url:
                continue
            if is_approved_registry_url(url):
                continue
            try:
                self._policy.enforce(url)
            except Exception:
                continue
            candidate = Candidate(
                identifier=url,
                identifier_kind="url",
                source_type="web",
                provider="tavily_web",
                title=title,
            )
            if classify(candidate) is not RelevanceLabel.COMPANY:
                continue
            score = fuzz.token_set_ratio(normalize_name(company_name), normalize_name(title))
            if score < 70:
                continue
            if best is None or score > best[0]:
                second_best = best[0] if best else 0.0
                best = (score, url)
            else:
                second_best = max(second_best, score)

        if best is None:
            return CompanyUrlResult(review_flags=["no_search_match"])
        if second_best and best[0] - second_best < 5:
            return CompanyUrlResult(
                official_url=None,
                confidence=Confidence.LOW,
                review_flags=["ambiguous_search_match"],
            )
        return CompanyUrlResult(
            official_url=best[1],
            confidence=Confidence.MEDIUM if best[0] >= 85 else Confidence.LOW,
            review_flags=[] if best[0] >= 85 else ["weak_search_match"],
        )


__all__ = ["CompanyUrlResolver", "CompanyUrlResult", "high_confidence_company"]
