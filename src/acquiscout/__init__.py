"""AcquiScout — source-backed, human-reviewed M&A prospect pipeline."""

from acquiscout.config import Settings, get_settings
from acquiscout.schemas import (
    Candidate,
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
    QueryHit,
    ReviewState,
    ScoreBreakdown,
    ScoreWeights,
    SearchTask,
    Sector,
    SourceRef,
    VerificationStatus,
)

__version__ = "0.1.0"

__all__ = [
    "Candidate",
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
    "QueryHit",
    "ReviewState",
    "ScoreBreakdown",
    "ScoreWeights",
    "SearchTask",
    "Sector",
    "Settings",
    "SourceRef",
    "VerificationStatus",
    "get_settings",
]
