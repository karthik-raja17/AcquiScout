"""Approved-source clients. Every outbound fetch is gated by policy.py."""

from acquiscout.sources.circuit import HostCircuitBreaker
from acquiscout.sources.policy import PolicyViolation, SourcePolicy
from acquiscout.sources.scrape import RedisCache, Scraper, ScrapeResult

__all__ = [
    "HostCircuitBreaker",
    "PolicyViolation",
    "RedisCache",
    "ScrapeResult",
    "Scraper",
    "SourcePolicy",
]
