import pytest
from pydantic import ValidationError

from acquiscout.schemas import (
    Confidence,
    Contact,
    ContactKind,
    County,
    EstimateMethod,
    FinancialEstimate,
    OwnershipProfile,
    Principal,
    Prospect,
    ReviewState,
    ScoreBreakdown,
    Sector,
    SourceRef,
    VerificationStatus,
)


def source(url: str = "https://example.com/registry") -> SourceRef:
    return SourceRef(url=url)


def estimate(value: int = 2_000_000) -> FinancialEstimate:
    return FinancialEstimate(
        value_usd=value,
        low_usd=int(value * 0.8),
        high_usd=int(value * 1.2),
        confidence=Confidence.MEDIUM,
        method=EstimateMethod.TRANSACTION_VOLUME,
        sources=[source()],
    )


def contact(status: VerificationStatus) -> Contact:
    return Contact(
        kind=ContactKind.EMAIL,
        value="owner@example.com",
        status=status,
        sources=[source()],
    )


def prospect(**overrides: object) -> Prospect:
    base: dict[str, object] = {
        "prospect_id": "p-1",
        "legal_name": "Sunshine Title LLC",
        "counties": [County.HILLSBOROUGH],
        "sector": Sector.TITLE_COMPANY,
    }
    base.update(overrides)
    return Prospect(**base)  # type: ignore[arg-type]


def test_minimal_prospect_is_pending() -> None:
    record = prospect()
    assert record.review_state is ReviewState.PENDING
    assert record.score is None
    assert record.is_qualified is False


def test_financial_value_requires_source() -> None:
    with pytest.raises(ValidationError):
        FinancialEstimate(
            value_usd=2_000_000,
            method=EstimateMethod.FEE_INCOME,
            confidence=Confidence.HIGH,
        )


def test_financial_value_requires_known_method() -> None:
    with pytest.raises(ValidationError):
        FinancialEstimate(value_usd=2_000_000, sources=[source()])


def test_band_must_contain_value() -> None:
    with pytest.raises(ValidationError):
        FinancialEstimate(
            value_usd=5_000_000,
            low_usd=1_000_000,
            high_usd=2_000_000,
            method=EstimateMethod.HEADCOUNT,
            sources=[source()],
        )


def test_score_component_capped_by_weight() -> None:
    with pytest.raises(ValidationError):
        ScoreBreakdown(
            financial_fit=31,
            management=20,
            ownership=15,
            contactability=15,
            market_strategic=10,
            data_confidence=10,
        )


def test_score_total_sums_components() -> None:
    score = ScoreBreakdown(
        financial_fit=28,
        management=18,
        ownership=12,
        contactability=13,
        market_strategic=8,
        data_confidence=9,
    )
    assert score.total == 88


def test_verified_requires_trusted_contact_and_financials() -> None:
    with pytest.raises(ValidationError):
        prospect(
            review_state=ReviewState.VERIFIED,
            revenue=estimate(),
            ebitda=estimate(600_000),
            contacts=[contact(VerificationStatus.SINGLE_SOURCE)],
            score=ScoreBreakdown(
                financial_fit=28,
                management=18,
                ownership=12,
                contactability=13,
                market_strategic=8,
                data_confidence=9,
            ),
        )


def test_verified_prospect_accepted() -> None:
    record = prospect(
        review_state=ReviewState.VERIFIED,
        revenue=estimate(),
        ebitda=estimate(600_000),
        contacts=[contact(VerificationStatus.CORROBORATED)],
        score=ScoreBreakdown(
            financial_fit=28,
            management=18,
            ownership=12,
            contactability=13,
            market_strategic=8,
            data_confidence=9,
        ),
    )
    assert record.review_state is ReviewState.VERIFIED
    assert record.is_qualified is True


def test_rejected_requires_reason() -> None:
    with pytest.raises(ValidationError):
        prospect(review_state=ReviewState.REJECTED)
    record = prospect(review_state=ReviewState.REJECTED, rejection_reason="Below revenue floor")
    assert record.rejection_reason == "Below revenue floor"


def test_ownership_management_team_needs_both_parts() -> None:
    only_principal = OwnershipProfile(
        legal_name="Sunshine Title LLC",
        principals=[Principal(name="Dana Reed", role="Owner", sources=[source()])],
    )
    assert only_principal.has_management_team is False
    complete = OwnershipProfile(
        legal_name="Sunshine Title LLC",
        principals=[Principal(name="Dana Reed", role="Owner", sources=[source()])],
        management_evidence=[source("https://example.com/team")],
    )
    assert complete.has_management_team is True
