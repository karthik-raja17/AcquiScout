"""Public Sunbiz/DBPR evidence resolution.

GovFiles is enrichment only. Evidence URLs must be public Sunbiz/DBPR pages.
Jina Reader is used because Sunbiz blocks direct httpx requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from acquiscout.config import Settings
from acquiscout.schemas import RegistryOfficer
from acquiscout.sources.policy import is_approved_registry_url
from acquiscout.sources.scrape import Scraper

DETAIL_LINK = re.compile(
    r"\[([^\]]+)\]\((https://search\.sunbiz\.org[^)]*SearchResultDetail[^)]*)\)",
    re.IGNORECASE,
)
DATE_FILED = re.compile(r"Date Filed\s+(\d{1,2}/\d{1,2}/\d{4})")
STATUS = re.compile(r"\bStatus\s+([A-Za-z ]+?)(?=\s+[A-Z][a-z]+\s|$)")
OFFICER = re.compile(r"Title\s+([^\n]+)\s*\n+\s*([^\n]+)")


@dataclass
class RegistryEvidence:
    public_detail_url: str | None = None
    legal_form: str | None = None
    status: str | None = None
    formed_on: date | None = None
    officers: list[RegistryOfficer] = field(default_factory=list)
    registered_address: str | None = None
    evidence_status: str = "failed"
    reason: str | None = None
    source: str | None = None
    http_status: int | None = None
    backend: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    text_chars: int = 0


def _source_for(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if host == "sunbiz.org" or host.endswith(".sunbiz.org"):
        return "sunbiz"
    if host == "myfloridalicense.com" or host.endswith(".myfloridalicense.com"):
        return "dbpr"
    if host == "dos.fl.gov" or host.endswith(".dos.fl.gov"):
        return "dfs"
    return None


def _evidence_status(status: str) -> str:
    if status in {
        "blocked",
        "rate_limited",
        "http_error",
        "network_error",
        "low_quality",
    }:
        return status
    if status == "not_found":
        return "http_error"
    return "failed"


def _after_markdown(text: str) -> str:
    marker = "Markdown Content:"
    return text.split(marker, 1)[1] if marker in text else text


def _parse_detail(text: str, detail_url: str) -> RegistryEvidence:
    body = _after_markdown(text)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    legal_form = lines[0] if lines else None
    status_match = STATUS.search(body)
    date_match = DATE_FILED.search(body)
    formed_on = None
    if date_match:
        month, day, year = (int(part) for part in date_match.group(1).split("/"))
        formed_on = date(month=month, day=day, year=year)

    officers: list[RegistryOfficer] = []
    section = body.split("Officer/Director Detail", 1)[-1].split("Annual Reports", 1)[0]
    for match in OFFICER.finditer(section):
        title = match.group(1).strip()
        name = match.group(2).strip()
        if name.lower().startswith(("address", "title")):
            continue
        officers.append(
            RegistryOfficer(
                name=name,
                title=title,
                role="officer",
            )
        )

    principal = re.search(r"Principal Address\s+(.*?)(?:\n\s*\n|Mailing Address)", body, re.S)
    registered = re.sub(r"\s+", " ", principal.group(1)).strip() if principal else None
    return RegistryEvidence(
        public_detail_url=detail_url,
        legal_form=legal_form,
        status=status_match.group(1).strip() if status_match else None,
        formed_on=formed_on,
        officers=officers,
        registered_address=registered,
        evidence_status="public_detail",
        text_chars=len(body),
    )


class RegistryEvidenceResolver:
    def __init__(self, settings: Settings, scraper: Scraper | None = None) -> None:
        self._settings = settings
        self._scraper = scraper or Scraper(settings)
        self._owns_scraper = scraper is None
        self.last_text: str = ""

    async def resolve(
        self,
        public_entity_url: str | None,
        entity_number: str,
    ) -> RegistryEvidence:
        if not public_entity_url:
            return RegistryEvidence(
                evidence_status="failed",
                reason="missing_public_entity_url",
            )
        if not is_approved_registry_url(public_entity_url):
            return RegistryEvidence(
                evidence_status="unsupported_source",
                reason="unapproved_registry_url",
                source=_source_for(public_entity_url),
            )
        source = _source_for(public_entity_url)
        search = await self._scraper.fetch(public_entity_url)
        self.last_text = search.text
        if not search.ok:
            return RegistryEvidence(
                evidence_status=_evidence_status(search.status),
                reason=search.reason,
                source=source,
                http_status=search.target_status or search.status_code,
                backend=search.backend,
                text_chars=len(search.text),
            )
        detail_url = self._find_detail_link(search.text, entity_number)
        if detail_url is None:
            return RegistryEvidence(
                public_detail_url=None,
                evidence_status="search_page_only",
                reason="no_detail_link",
                source=source,
                http_status=search.target_status or search.status_code,
                backend=search.backend,
                text_chars=len(search.text),
            )
        detail = await self._scraper.fetch(detail_url)
        self.last_text = detail.text
        if not detail.ok:
            return RegistryEvidence(
                public_detail_url=detail_url,
                evidence_status=_evidence_status(detail.status),
                reason=detail.reason,
                source=source,
                http_status=detail.target_status or detail.status_code,
                backend=detail.backend,
                text_chars=len(detail.text),
            )
        return replace(
            _parse_detail(detail.text, detail_url),
            source=source,
            http_status=detail.target_status or detail.status_code,
            backend=detail.backend,
        )

    @staticmethod
    def _find_detail_link(text: str, entity_number: str) -> str | None:
        first: str | None = None
        for line in text.splitlines():
            for match in DETAIL_LINK.finditer(line):
                first = first or match.group(2)
                if (
                    entity_number.lower() in line.lower()
                    or entity_number.lower() in match.group(2).lower()
                ):
                    return match.group(2)
        return first

    async def aclose(self) -> None:
        if self._owns_scraper:
            await self._scraper.aclose()


__all__ = ["RegistryEvidence", "RegistryEvidenceResolver"]
