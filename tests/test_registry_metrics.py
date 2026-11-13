from __future__ import annotations

from acquiscout.schemas import CompanyCandidate, RegistryProfile
from acquiscout.services.registry_metrics import summarize_registry_evidence


def test_registry_metrics_count_status_and_source() -> None:
    companies = [
        CompanyCandidate(
            company_key="us_fl:1",
            company_name="Sunbiz Co",
            registry_ids=["us_fl:1"],
            registry_profile=RegistryProfile(
                evidence_status="public_detail",
                evidence_source="sunbiz",
            ),
        ),
        CompanyCandidate(
            company_key="us_fl:2",
            company_name="DBPR Co",
            registry_ids=["us_fl:2"],
            registry_profile=RegistryProfile(
                evidence_status="public_detail",
                evidence_source="dbpr",
            ),
        ),
    ]
    dropped = [
        {
            "reason": "registry_evidence_blocked",
            "evidence_status": "blocked",
            "evidence_source": "sunbiz",
        },
        {
            "reason": "missing_sunbiz_identity_evidence",
            "evidence_status": "public_detail",
            "evidence_source": "dfs",
        },
    ]

    summary = summarize_registry_evidence(companies, dropped)

    assert summary["profiles"] == 2
    assert summary["status_counts"] == {"blocked": 1, "public_detail": 3}
    assert summary["source_counts"] == {"dbpr": 1, "dfs": 1, "sunbiz": 2}
    assert summary["public_detail_by_source"] == {"dbpr": 1, "sunbiz": 1}
    assert summary["dropped_by_reason"] == {
        "missing_sunbiz_identity_evidence": 1,
        "registry_evidence_blocked": 1,
    }
