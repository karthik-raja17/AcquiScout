"""Summaries for registry evidence by status and source."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def _value(item: object, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def summarize_registry_evidence(
    companies: Iterable[object],
    dropped: Iterable[object],
) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    public_by_source: Counter[str] = Counter()
    dropped_by_reason: Counter[str] = Counter()
    profiles = 0

    for company in companies:
        profile = _value(company, "registry_profile")
        if profile is None:
            continue
        profiles += 1
        status = str(_value(profile, "evidence_status") or "none")
        source = str(_value(profile, "evidence_source") or "unknown")
        statuses[status] += 1
        sources[source] += 1
        if status == "public_detail":
            public_by_source[source] += 1

    for item in dropped:
        status = str(_value(item, "evidence_status") or "none")
        source = str(_value(item, "evidence_source") or "unknown")
        statuses[status] += 1
        sources[source] += 1
        dropped_by_reason[str(_value(item, "reason") or "unknown")] += 1

    return {
        "profiles": profiles,
        "status_counts": dict(sorted(statuses.items())),
        "source_counts": dict(sorted(sources.items())),
        "public_detail_by_source": dict(sorted(public_by_source.items())),
        "dropped_by_reason": dict(sorted(dropped_by_reason.items())),
    }


__all__ = ["summarize_registry_evidence"]
