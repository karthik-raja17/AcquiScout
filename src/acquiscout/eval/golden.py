from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from acquiscout.schemas import Confidence, ContactKind, County, EstimateMethod, Sector

COMPONENT_CAPS = {
    "score_financial": 30,
    "score_management": 20,
    "score_ownership": 15,
    "score_contactability": 15,
    "score_market": 10,
    "score_data_confidence": 10,
}

SENTINELS = {"NOT_FOUND"}


class ContactTruth(StrEnum):
    VALID = "valid"
    STALE = "stale"
    INVALID = "invalid"


class ContactCheckMethod(StrEnum):
    CALLED = "called"
    EMAIL_BOUNCED = "email_bounced"
    LINKEDIN_VERIFIED = "linkedin_verified"
    WEBSITE_ONLY = "website_only"


class Verdict(StrEnum):
    QUALIFIED = "qualified"
    SEMI_QUALIFIED = "semi_qualified"
    REJECTED = "rejected"


class GoldenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    legal_name: str
    dba: str | None = None
    website: str | None = None
    primary_county: County
    also_serves_counties: str | None = None
    sector: Sector
    true_entity_id: str

    revenue_low_usd: int | None = None
    revenue_high_usd: int | None = None
    revenue_confidence: Confidence
    revenue_method: EstimateMethod
    revenue_source_url: str | None = None

    ebitda_low_usd: int | None = None
    ebitda_high_usd: int | None = None
    ebitda_confidence: Confidence
    ebitda_method: EstimateMethod
    ebitda_source_url: str | None = None

    contact_kind: ContactKind
    contact_value: str
    contact_label: str | None = None
    contact_truth: ContactTruth
    contact_source_url: str
    contact_checked_at: date
    contact_check_method: ContactCheckMethod

    principal_name: str | None = None
    principal_role: str | None = None
    principal_source_url: str | None = None
    management_evidence_url: str | None = None

    employee_headcount: str | None = None
    founded_year: int | None = None

    human_verdict: Verdict
    rejection_reason: str | None = None

    primary_source_url: str
    researched_at: date

    score_financial: int = Field(ge=0, le=COMPONENT_CAPS["score_financial"])
    score_management: int = Field(ge=0, le=COMPONENT_CAPS["score_management"])
    score_ownership: int = Field(ge=0, le=COMPONENT_CAPS["score_ownership"])
    score_contactability: int = Field(ge=0, le=COMPONENT_CAPS["score_contactability"])
    score_market: int = Field(ge=0, le=COMPONENT_CAPS["score_market"])
    score_data_confidence: int = Field(ge=0, le=COMPONENT_CAPS["score_data_confidence"])

    @computed_field
    @property
    def human_score_total(self) -> int:
        return sum(getattr(self, field) for field in COMPONENT_CAPS)

    @model_validator(mode="after")
    def _rejected_needs_reason(self) -> Self:
        if self.human_verdict == Verdict.REJECTED and not self.rejection_reason:
            raise ValueError("rejection_reason is required when human_verdict is 'rejected'")
        return self


def _clean(value: object) -> object:
    if isinstance(value, str):
        text = value.strip()
        if not text or text.upper() in SENTINELS:
            return None
        return text
    return value


def parse_sheet_row(raw: Mapping[str, object]) -> GoldenRecord:
    data = {key: _clean(value) for key, value in raw.items()}
    stored_total = data.pop("human_score_total", None)
    record = GoldenRecord.model_validate(data)
    if stored_total is None:
        raise ValueError("human_score_total is blank")
    if isinstance(stored_total, bool) or not isinstance(stored_total, (int, float)):
        raise ValueError("human_score_total must be numeric")
    if int(stored_total) != record.human_score_total:
        raise ValueError(
            f"stored human_score_total {stored_total} != derived {record.human_score_total}"
        )
    return record
