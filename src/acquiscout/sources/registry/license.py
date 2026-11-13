"""DBPR/DFS license-page evidence sources."""

from __future__ import annotations

import re

from acquiscout.sources.registry.base import (
    RegistryEvidence,
    RegistrySource,
    after_markdown,
    failure_evidence,
    parse_date,
)
from acquiscout.sources.scrape import Scraper

LICENSE_NUMBER = re.compile(
    r"(?:License|Licensure)\s*(?:Number|No\.?|#)\s*[:#]?\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
LICENSE_TYPE = re.compile(
    r"(?:License Type|Licensure Type|Profession|Occupation)\s*[:#]?\s*([^\n]+)",
    re.IGNORECASE,
)
LICENSE_STATUS = re.compile(
    r"(?:License Status|Status)\s*[:#]?\s*([A-Za-z][A-Za-z ]*?)(?:\n|$)",
    re.IGNORECASE,
)
EXPIRATION = re.compile(
    r"(?:Expiration Date|Expires|Expiration)\s*[:#]?\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
LICENSEE = re.compile(
    r"(?:Licensee Name|Business Name|Licensee)\s*[:#]?\s*([^\n]+)",
    re.IGNORECASE,
)
ADDRESS = re.compile(
    r"(?:Business Address|Address|Mailing Address)\s*[:#]?\s*(.*?)(?:\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split()).strip(" :#|-")
    return cleaned or None


def _parse_license(text: str, url: str, source: str) -> RegistryEvidence:
    body = after_markdown(text)
    number = LICENSE_NUMBER.search(body)
    license_type = LICENSE_TYPE.search(body)
    status = LICENSE_STATUS.search(body)
    expiration = EXPIRATION.search(body)
    licensee = LICENSEE.search(body)
    address = ADDRESS.search(body)
    registered = _clean(address.group(1)) if address else None

    evidence = RegistryEvidence(
        public_detail_url=url,
        license_number=_clean(number.group(1)) if number else None,
        license_type=_clean(license_type.group(1)) if license_type else None,
        license_status=_clean(status.group(1)) if status else None,
        license_expiration=parse_date(expiration.group(1)) if expiration else None,
        licensee_name=_clean(licensee.group(1)) if licensee else None,
        registered_address=registered,
        source=source,
        text_chars=len(body),
    )
    if any(
        (
            evidence.license_number,
            evidence.license_type,
            evidence.license_status,
        )
    ):
        evidence.evidence_status = "public_detail"
    elif "search" in body.lower():
        evidence.evidence_status = "search_page_only"
        evidence.reason = "no_license_detail"
    else:
        evidence.evidence_status = "parse_failed"
        evidence.reason = "no_license_fields"
    return evidence


class LicenseSource(RegistrySource):
    async def resolve(
        self,
        public_entity_url: str,
        entity_number: str,
        scraper: Scraper,
    ) -> RegistryEvidence:
        fetched = await scraper.fetch(public_entity_url)
        self.last_text = fetched.text
        if not fetched.ok:
            return failure_evidence(fetched, self.name)
        parsed = _parse_license(fetched.text, public_entity_url, self.name)
        parsed.http_status = fetched.target_status or fetched.status_code
        parsed.backend = fetched.backend
        return parsed


class DBPREvidenceSource(LicenseSource):
    name = "dbpr"
    apex = "myfloridalicense.com"


class DFSEvidenceSource(LicenseSource):
    name = "dfs"
    apex = "dos.fl.gov"


__all__ = ["DBPREvidenceSource", "DFSEvidenceSource", "LicenseSource"]
