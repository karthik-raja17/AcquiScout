"""Approved-source enforcement. Runs before any request leaves the process."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from acquiscout.schemas import Candidate

DENIED_HOSTS: frozenset[str] = frozenset(
    {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "glassdoor.com",
        "bbb.org",
        "yelp.com",
        "yellowpages.com",
        "wikipedia.org",
        "reddit.com",
        "quora.com",
    }
)

DENIED_SUFFIXES: tuple[str, ...] = tuple(f".{host}" for host in DENIED_HOSTS)

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

APPROVED_REGISTRY_HOSTS: frozenset[str] = frozenset(
    {
        "myfloridalicense.com",  # DBPR: apex covers www + www2
        "sunbiz.org",  # Sunbiz: apex covers search. + efile.
        "dos.fl.gov",
    }
)


def is_approved_registry_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == approved or normalized.endswith(f".{approved}")
        for approved in APPROVED_REGISTRY_HOSTS
    )


def is_approved_registry_url(url: str) -> bool:
    return is_approved_registry_host(urlsplit(url).hostname or "")


BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}
)

DENY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"/wp-admin(?:/|$)",
        r"/login(?:/|$)",
        r"/signup(?:/|$)",
        r"/tag(?:/|$)",
        r"/category(?:/|$)",
        r"/author(?:/|$)",
    )
)

REGISTRY_PDF_HOSTS: frozenset[str] = frozenset(
    {"sunbiz.org", "search.sunbiz.org", "dos.fl.gov", "efile.sunbiz.org", "flrules.org"}
)

GENERIC_PDF_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (r"/brochure", r"/flyer", r"/menu", r"/catalog", r"/whitepaper", r"/ebook")
)

Classification = Literal["allow", "deny", "registry_pdf"]


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    classification: Classification = "allow"


class PolicyViolation(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"blocked by source policy: {reason} ({url})")
        self.url = url
        self.reason = reason


def _host_matches(host: str, domains: frozenset[str]) -> bool:
    return host in domains or host.endswith(tuple(f".{domain}" for domain in domains))


class SourcePolicy:
    """Deny-list, path heuristics, PDF classification, and SSRF guard."""

    def __init__(self, denied_hosts: frozenset[str] = DENIED_HOSTS) -> None:
        self._denied = denied_hosts

    def check(self, url: str) -> PolicyDecision:
        parts = urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            return PolicyDecision(False, f"scheme not allowed: {parts.scheme or 'none'}", "deny")

        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            return PolicyDecision(False, "missing host", "deny")
        if host in BLOCKED_HOSTNAMES:
            return PolicyDecision(False, f"blocked hostname: {host}", "deny")
        if _host_matches(host, self._denied):
            return PolicyDecision(False, f"prohibited domain: {host}", "deny")

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            return PolicyDecision(False, "private or loopback address", "deny")

        path = parts.path or "/"
        if any(pattern.search(path) for pattern in DENY_PATTERNS):
            return PolicyDecision(False, f"prohibited path: {path}", "deny")

        if path.lower().endswith(".pdf"):
            if _host_matches(host, REGISTRY_PDF_HOSTS):
                return PolicyDecision(True, "registry_pdf", "registry_pdf")
            if any(pattern.search(path.lower()) for pattern in GENERIC_PDF_PATTERNS):
                return PolicyDecision(False, f"marketing pdf: {path}", "deny")
            return PolicyDecision(True, "unclassified_pdf", "registry_pdf")

        return PolicyDecision(True)

    def classify(self, url: str) -> Classification:
        return self.check(url).classification

    def classify_candidate(self, candidate: Candidate) -> PolicyDecision:
        if candidate.identifier_kind == "registry_id":
            return PolicyDecision(True)
        return self.check(candidate.identifier)

    def enforce(self, url: str) -> None:
        decision = self.check(url)
        if not decision.allowed:
            raise PolicyViolation(url, decision.reason or "disallowed")

    def is_allowed(self, url: str) -> bool:
        return self.check(url).allowed


__all__ = [
    "ALLOWED_SCHEMES",
    "APPROVED_REGISTRY_HOSTS",
    "BLOCKED_HOSTNAMES",
    "Classification",
    "DENIED_HOSTS",
    "DENIED_SUFFIXES",
    "DENY_PATTERNS",
    "GENERIC_PDF_PATTERNS",
    "PolicyDecision",
    "PolicyViolation",
    "REGISTRY_PDF_HOSTS",
    "SourcePolicy",
    "is_approved_registry_host",
    "is_approved_registry_url",
]
