"""Registry evidence data and shared source helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from acquiscout.schemas import RegistryOfficer
from acquiscout.sources.scrape import Scraper, ScrapeResult

MARKDOWN_MARKER = "Markdown Content:"
DATE_PATTERN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


@dataclass
class RegistryEvidence:
    public_detail_url: str | None = None
    legal_form: str | None = None
    status: str | None = None
    formed_on: date | None = None
    officers: list[RegistryOfficer] = field(default_factory=list)
    registered_address: str | None = None
    license_number: str | None = None
    license_type: str | None = None
    license_status: str | None = None
    license_expiration: date | None = None
    licensee_name: str | None = None
    evidence_status: str = "failed"
    reason: str | None = None
    source: str | None = None
    http_status: int | None = None
    backend: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    text_chars: int = 0


class RegistrySource:
    name = "unknown"
    apex = ""

    def __init__(self) -> None:
        self.last_text = ""

    def supports(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return host == self.apex or host.endswith(f".{self.apex}")

    async def resolve(
        self,
        public_entity_url: str,
        entity_number: str,
        scraper: Scraper,
    ) -> RegistryEvidence:
        raise NotImplementedError


def after_markdown(text: str) -> str:
    return text.split(MARKDOWN_MARKER, 1)[1] if MARKDOWN_MARKER in text else text


def parse_date(value: str) -> date | None:
    match = DATE_PATTERN.search(value)
    if match is None:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(month=month, day=day, year=year)
    except ValueError:
        return None


def evidence_status(status: str) -> str:
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


def failure_evidence(
    fetched: ScrapeResult,
    source: str,
    *,
    public_detail_url: str | None = None,
) -> RegistryEvidence:
    return RegistryEvidence(
        public_detail_url=public_detail_url,
        evidence_status=evidence_status(fetched.status),
        reason=fetched.reason,
        source=source,
        http_status=fetched.target_status or fetched.status_code,
        backend=fetched.backend,
        text_chars=len(fetched.text),
    )


__all__ = [
    "RegistryEvidence",
    "RegistrySource",
    "after_markdown",
    "evidence_status",
    "failure_evidence",
    "parse_date",
]
