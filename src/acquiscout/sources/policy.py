"""Approved-source enforcement. Runs before any request leaves the process."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

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
    }
)

DENIED_SUFFIXES: tuple[str, ...] = tuple(f".{host}" for host in DENIED_HOSTS)

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"}
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class PolicyViolation(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"blocked by source policy: {reason} ({url})")
        self.url = url
        self.reason = reason


class SourcePolicy:
    """Deny-list plus SSRF guard. Deny wins over any allow-list."""

    def __init__(self, denied_hosts: frozenset[str] = DENIED_HOSTS) -> None:
        self._denied = denied_hosts

    def check(self, url: str) -> PolicyDecision:
        parts = urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            return PolicyDecision(False, f"scheme not allowed: {parts.scheme or 'none'}")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            return PolicyDecision(False, "missing host")
        if host in BLOCKED_HOSTNAMES:
            return PolicyDecision(False, f"blocked hostname: {host}")
        if host in self._denied or host.endswith(tuple(f".{d}" for d in self._denied)):
            return PolicyDecision(False, f"prohibited domain: {host}")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return PolicyDecision(True)
        blocked = (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )
        if blocked:
            return PolicyDecision(False, "private or loopback address")
        return PolicyDecision(True)

    def enforce(self, url: str) -> None:
        decision = self.check(url)
        if not decision.allowed:
            raise PolicyViolation(url, decision.reason or "disallowed")

    def is_allowed(self, url: str) -> bool:
        return self.check(url).allowed


__all__ = [
    "ALLOWED_SCHEMES",
    "DENIED_HOSTS",
    "DENIED_SUFFIXES",
    "PolicyDecision",
    "PolicyViolation",
    "SourcePolicy",
]
