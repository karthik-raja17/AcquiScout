"""Sunbiz corporate-registry evidence source."""

from __future__ import annotations

import re
from dataclasses import replace

from acquiscout.schemas import RegistryOfficer
from acquiscout.sources.registry.base import (
    RegistryEvidence,
    RegistrySource,
    after_markdown,
    failure_evidence,
    parse_date,
)
from acquiscout.sources.scrape import Scraper

DETAIL_LINK = re.compile(
    r"\[([^\]]+)\]\((https://search\.sunbiz\.org[^)]*SearchResultDetail[^)]*)\)",
    re.IGNORECASE,
)
DATE_FILED = re.compile(r"Date Filed\s+(\d{1,2}/\d{1,2}/\d{4})")
STATUS = re.compile(r"\bStatus\s+([A-Za-z ]+?)(?=\s+[A-Z][a-z]+\s|$)")
OFFICER = re.compile(r"Title\s+([^\n]+)\s*\n+\s*([^\n]+)")


def _parse_detail(text: str, detail_url: str) -> RegistryEvidence:
    body = after_markdown(text)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    legal_form = lines[0] if lines else None
    status_match = STATUS.search(body)
    date_match = DATE_FILED.search(body)

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
        formed_on=parse_date(date_match.group(1)) if date_match else None,
        officers=officers,
        registered_address=registered,
        evidence_status="public_detail",
        text_chars=len(body),
    )


class SunbizSource(RegistrySource):
    name = "sunbiz"
    apex = "sunbiz.org"

    async def resolve(
        self,
        public_entity_url: str,
        entity_number: str,
        scraper: Scraper,
    ) -> RegistryEvidence:
        search = await scraper.fetch(public_entity_url)
        self.last_text = search.text
        if not search.ok:
            return failure_evidence(search, self.name)

        detail_url = self._find_detail_link(search.text, entity_number)
        if detail_url is None:
            return RegistryEvidence(
                evidence_status="search_page_only",
                reason="no_detail_link",
                source=self.name,
                http_status=search.target_status or search.status_code,
                backend=search.backend,
                text_chars=len(search.text),
            )

        detail = await scraper.fetch(detail_url)
        self.last_text = detail.text
        if not detail.ok:
            return failure_evidence(
                detail,
                self.name,
                public_detail_url=detail_url,
            )
        return replace(
            _parse_detail(detail.text, detail_url),
            source=self.name,
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


__all__ = ["SunbizSource"]
