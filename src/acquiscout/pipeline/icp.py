"""Search vocabulary and ICP priors.

The hints are domain vocabulary, not thresholds fitted to the golden set.
`employee_headcount` and `founded_year` are context fields in golden v2 and
are deliberately not used as qualification gates.
"""

from __future__ import annotations

from typing import Any

from acquiscout.config import Settings

SEARCH_HINTS: dict[str, Any] = {
    "sector_terms": {
        "title_company": [
            "title company",
            "title agency",
            "title services",
            "escrow agent",
            "abstract agency",
        ],
        "property_management": [
            "property management",
            "HOA management",
            "community association management",
            "rental management",
        ],
    },
    "geography_terms": [
        "Tampa",
        "Hillsborough County",
        "Pinellas County",
        "Pasco County",
        "Manatee County",
        "Sarasota County",
        "Hernando County",
    ],
    "entity_suffixes": ["LLC", "INC", "PA", "LP"],
    "registry_terms": [
        "Sunbiz",
        "Florida Division of Corporations",
        "registered agent",
        "Florida Department of State",
    ],
    "association_terms": [
        "FLTA",
        "Florida Land Title Association",
        "NARPM",
        "National Association of Residential Property Managers",
        "CAI",
        "Community Associations Institute",
    ],
    "evidence_indicators": [
        "team page",
        "about us",
        "our team",
        "leadership",
        "meet the team",
        "contact us",
    ],
}


def build_icp(settings: Settings) -> dict[str, Any]:
    return {
        "sectors": [sector.value for sector in settings.sectors],
        "counties": [county.value for county in settings.counties],
        "hints_version": settings.search_hints_version,
        "notes": (
            "Established local operator with a web presence, a public registry "
            "record, and identifiable management. Headcount and founding year "
            "are context only."
        ),
    }


__all__ = ["SEARCH_HINTS", "build_icp"]
