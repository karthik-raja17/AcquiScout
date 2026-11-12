"""Deterministic relevance classification for discovery candidates."""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlsplit

from acquiscout.schemas import Candidate

DIRECTORY_HOSTS: frozenset[str] = frozenset(
    {
        "bbb.org",
        "crunchbase.com",
        "expertise.com",
        "facebook.com",
        "hoamanagement.com",
        "indeed.com",
        "leadiq.com",
        "linkedin.com",
        "mapquest.com",
        "manta.com",
        "rocketreach.co",
        "signalhire.com",
        "yellowpages.com",
        "yelp.com",
        "zoominfo.com",
    }
)

GOVERNMENT_HOSTS: frozenset[str] = frozenset(
    {
        "pinellas.gov",
        "pascotaxes.com",
        "hillsboroughcounty.org",
        "tampagov.net",
        "stpete.org",
        "clearwaterfl.gov",
        "pascocountyfl.net",
        "joinhcso.com",
        "floridarevenue.com",
        "sunbiz.org",
    }
)

NEWS_HOSTS: frozenset[str] = frozenset(
    {
        "bizjournals.com",
        "bloomberg.com",
        "fox13news.com",
        "prnewswire.com",
        "reuters.com",
        "tampabay.com",
        "wfla.com",
        "wsj.com",
    }
)

_DIRECTORY_TITLE_SUFFIXES = re.compile(
    r"\s*[|–—-]\s*"
    r"(LeadIQ|MapQuest|Expertise\.com|Indeed|RocketReach|YellowPages|BBB|LinkedIn|Facebook)"
    r".*$",
    re.IGNORECASE,
)
_OVERVIEW_SUFFIX = re.compile(
    r"\s+Company Overview, Contact Details & Competitors.*$", re.IGNORECASE
)
_GENERIC_PIPE_SUFFIX = re.compile(r"\s*\|.*$")
_LEADING_HOME = re.compile(r"^Home\s*[|–—-]\s*", re.IGNORECASE)
_TRAILING_REGION = re.compile(r",\s*(FL|Florida)\s*$", re.IGNORECASE)
_TRAILING_RANK = re.compile(r"\s*-\s*#?\d+.*$")


class RelevanceLabel(StrEnum):
    COMPANY = "company"
    DIRECTORY = "directory"
    GOVERNMENT = "government"
    NEWS = "news"
    REGISTRY = "registry"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _matches(host: str, domains: frozenset[str]) -> bool:
    return host in domains or any(host.endswith(f".{domain}") for domain in domains)


def classify(candidate: Candidate) -> RelevanceLabel:
    if candidate.identifier_kind == "registry_id":
        return RelevanceLabel.REGISTRY
    host = _host(candidate.identifier)
    if host.endswith(".gov") or _matches(host, GOVERNMENT_HOSTS):
        return RelevanceLabel.GOVERNMENT
    if _matches(host, DIRECTORY_HOSTS):
        return RelevanceLabel.DIRECTORY
    if _matches(host, NEWS_HOSTS):
        return RelevanceLabel.NEWS
    return RelevanceLabel.COMPANY


def clean_company_name(title: str | None, url: str) -> str:
    text = (title or "").strip()
    if not text:
        host = _host(url)
        text = host.split(".")[0].replace("-", " ").replace("_", " ").title()
    text = _DIRECTORY_TITLE_SUFFIXES.sub("", text)
    text = _OVERVIEW_SUFFIX.sub("", text)
    text = _GENERIC_PIPE_SUFFIX.sub("", text)
    text = _LEADING_HOME.sub("", text)
    text = _TRAILING_REGION.sub("", text)
    text = _TRAILING_RANK.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -|–—")
    return text[:120] or "Unknown company"


__all__ = [
    "DIRECTORY_HOSTS",
    "GOVERNMENT_HOSTS",
    "NEWS_HOSTS",
    "RelevanceLabel",
    "classify",
    "clean_company_name",
]
