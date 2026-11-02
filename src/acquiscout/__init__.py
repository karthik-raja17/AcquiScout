"""AcquiScout — source-backed, human-reviewed M&A prospect pipeline."""

from acquiscout.config import Settings, get_settings
from acquiscout.schemas import (
    Confidence,
    Contact,
    ContactKind,
    County,
    CrmStatus,
    EstimateMethod,
    FinancialEstimate,
    OwnershipProfile,
    Principal,
    Prospect,
    ReviewState,
    ScoreBreakdown,
    ScoreWeights,
    Sector,
    SourceRef,
    VerificationStatus,
)

__version__ = "0.1.0"

__all__ = [
    "Confidence",
    "Contact",
    "ContactKind",
    "County",
    "CrmStatus",
    "EstimateMethod",
    "FinancialEstimate",
    "OwnershipProfile",
    "Principal",
    "Prospect",
    "ReviewState",
    "ScoreBreakdown",
    "ScoreWeights",
    "Sector",
    "Settings",
    "SourceRef",
    "VerificationStatus",
    "get_settings",
]
