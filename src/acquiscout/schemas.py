"""Pydantic v2 domain models for AcquiScout.

These models are the contract between pipeline stages. A stage may not emit a
field that is not defined here — extend the schema first.

Invariant enforced here, not by reviewer discipline: a financial value is
invalid without a method and at least one source, and a prospect cannot be
marked verified unless its financial claims are sourced and at least one
contact is corroborated or human-audited.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    computed_field,
    field_validator,
    model_validator,
)


def _now() -> datetime:
    return datetime.now(UTC)


class Sector(StrEnum):
    TITLE_COMPANY = "title_company"
    PROPERTY_MANAGEMENT = "property_management"


class County(StrEnum):
    HILLSBOROUGH = "hillsborough"
    PINELLAS = "pinellas"
    PASCO = "pasco"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EstimateMethod(StrEnum):
    OFFICE_COUNT = "office_count"
    HEADCOUNT = "headcount"
    TRANSACTION_VOLUME = "transaction_volume"
    UNITS_UNDER_MANAGEMENT = "units_under_management"
    FEE_INCOME = "fee_income"
    WEBSITE_CLAIM = "website_claim"
    REGISTRY_FILING = "registry_filing"
    PUBLIC_LISTING = "public_listing"
    BENCHMARK_RATIO = "benchmark_ratio"
    HUMAN_OVERRIDE = "human_override"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SINGLE_SOURCE = "single_source"
    CORROBORATED = "corroborated"
    HUMAN_AUDITED = "human_audited"
    STALE = "stale"
    INVALID = "invalid"


TRUSTED_CONTACT_STATUSES = frozenset(
    {VerificationStatus.CORROBORATED, VerificationStatus.HUMAN_AUDITED}
)


class ContactKind(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    WEBSITE = "website"
    LINKEDIN = "linkedin"


class ReviewState(StrEnum):
    PENDING = "pending"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CrmStatus(StrEnum):
    NEW = "new"
    PRIORITIZED = "prioritized"
    REVIEWING = "reviewing"
    EXPORTED = "exported"
    PASSED = "passed"


class SourceRef(BaseModel):
    """Provenance for a single claim. Untrusted text never lives here."""

    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    title: str | None = None
    retrieved_at: datetime = Field(default_factory=_now)
    extractor_version: str = "v1"


class FinancialEstimate(BaseModel):
    """A revenue or EBITDA proxy. Never render this as an audited fact."""

    value_usd: int | None = Field(default=None, ge=0)
    low_usd: int | None = Field(default=None, ge=0)
    high_usd: int | None = Field(default=None, ge=0)
    confidence: Confidence = Confidence.LOW
    method: EstimateMethod = EstimateMethod.UNKNOWN
    notes: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _check_auditability(self) -> Self:
        if self.value_usd is not None and not self.sources:
            raise ValueError("a financial value requires at least one source")
        if self.method is EstimateMethod.UNKNOWN and self.value_usd is not None:
            raise ValueError("a financial value requires a non-unknown method")
        if self.low_usd is not None and self.high_usd is not None and self.low_usd > self.high_usd:
            raise ValueError("low_usd must be <= high_usd")
        if self.value_usd is not None and self.low_usd is not None:
            if self.value_usd < self.low_usd:
                raise ValueError("value_usd must be within the band")
        if self.value_usd is not None and self.high_usd is not None:
            if self.value_usd > self.high_usd:
                raise ValueError("value_usd must be within the band")
        return self

    @property
    def is_band(self) -> bool:
        return self.low_usd is not None and self.high_usd is not None


class Contact(BaseModel):
    kind: ContactKind
    value: str = Field(min_length=1)
    label: str | None = None
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    sources: list[SourceRef] = Field(min_length=1)
    last_checked_at: datetime = Field(default_factory=_now)

    @property
    def is_trusted(self) -> bool:
        return self.status in TRUSTED_CONTACT_STATUSES


class Principal(BaseModel):
    name: str = Field(min_length=1)
    role: str | None = None
    sources: list[SourceRef] = Field(min_length=1)


class OwnershipProfile(BaseModel):
    legal_name: str = Field(min_length=1)
    dba: str | None = None
    entity_type: str | None = None
    principals: list[Principal] = Field(default_factory=list)
    management_evidence: list[SourceRef] = Field(default_factory=list)

    @property
    def has_management_team(self) -> bool:
        """Named principal plus independent management evidence."""
        return bool(self.principals) and bool(self.management_evidence)


class ScoreWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    financial_fit: int = 30
    management: int = 20
    ownership: int = 15
    contactability: int = 15
    market_strategic: int = 10
    data_confidence: int = 10

    @model_validator(mode="after")
    def _sum_to_100(self) -> Self:
        total = sum(self.model_dump().values())
        if total != 100:
            raise ValueError(f"score weights must sum to 100, got {total}")
        return self


class ScoreBreakdown(BaseModel):
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    financial_fit: float = Field(ge=0)
    management: float = Field(ge=0)
    ownership: float = Field(ge=0)
    contactability: float = Field(ge=0)
    market_strategic: float = Field(ge=0)
    data_confidence: float = Field(ge=0)
    rationale: str | None = None
    risk_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bounded_by_weights(self) -> Self:
        for name, cap in self.weights.model_dump().items():
            if getattr(self, name) > cap:
                raise ValueError(f"{name} score exceeds its weight ({cap})")
        return self

    @computed_field
    @property
    def total(self) -> float:
        return round(
            self.financial_fit
            + self.management
            + self.ownership
            + self.contactability
            + self.market_strategic
            + self.data_confidence,
            2,
        )


class Prospect(BaseModel):
    """One company in the acquisition pipeline."""

    prospect_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    dba: str | None = None
    website: HttpUrl | None = None
    counties: list[County] = Field(min_length=1)
    sector: Sector
    addresses: list[str] = Field(default_factory=list)

    revenue: FinancialEstimate | None = None
    ebitda: FinancialEstimate | None = None
    ownership: OwnershipProfile | None = None
    contacts: list[Contact] = Field(default_factory=list)
    score: ScoreBreakdown | None = None

    dedup_cluster_id: str | None = None
    duplicate_of: str | None = None

    review_state: ReviewState = ReviewState.PENDING
    rejection_reason: str | None = None
    crm_status: CrmStatus = CrmStatus.NEW

    sources: list[SourceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _check_review_contract(self) -> Self:
        if self.review_state is ReviewState.VERIFIED:
            if self.rejection_reason is not None:
                raise ValueError("a verified prospect cannot carry a rejection reason")
            if self.score is None:
                raise ValueError("a verified prospect requires a score")
            for name, estimate in (("revenue", self.revenue), ("ebitda", self.ebitda)):
                if estimate is None or estimate.value_usd is None:
                    raise ValueError(f"a verified prospect requires a {name} estimate")
                if not estimate.sources:
                    raise ValueError(f"a verified prospect requires sourced {name}")
            if not any(contact.is_trusted for contact in self.contacts):
                raise ValueError("a verified prospect requires a corroborated contact")
        if self.review_state is ReviewState.REJECTED and not (
            self.rejection_reason and self.rejection_reason.strip()
        ):
            raise ValueError("a rejected prospect requires a rejection reason")
        return self

    @property
    def is_qualified(self) -> bool:
        if self.score is None or self.revenue is None or self.ebitda is None:
            return False
        return (
            self.review_state is not ReviewState.REJECTED
            and self.revenue.value_usd is not None
            and self.ebitda.value_usd is not None
        )


class QueryHit(BaseModel):
    """One search query that surfaced a candidate."""

    query: str = Field(min_length=1)
    search_plan_index: int = Field(ge=0)
    provider: str = Field(min_length=1)
    found_at: datetime = Field(default_factory=_now)


class Candidate(BaseModel):
    """A deduplicated discovery result with its provenance preserved."""

    identifier: str = Field(min_length=1)
    identifier_kind: Literal["url", "registry_id"]
    registry_jurisdiction: str | None = None
    source_type: Literal["web", "registry"]
    provider: str = Field(min_length=1)
    title: str | None = None
    snippet: str | None = None
    crawl_url: str | None = None
    public_entity_url: str | None = None
    resolution_status: Literal["none", "resolved", "failed"] = "none"
    review_flags: list[str] = Field(default_factory=list)
    found_by: list[QueryHit] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=_now)

    @field_validator("crawl_url", "public_entity_url")
    @classmethod
    def _validate_crawl_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(TypeAdapter(HttpUrl).validate_python(value))

    @model_validator(mode="after")
    def _identifier_matches_kind(self) -> Self:
        if self.identifier_kind == "url":
            parts = urlsplit(self.identifier)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("url candidates require an http(s) identifier")
        return self


class SearchTask(BaseModel):
    """One validated task emitted by the planner."""

    task_id: str = Field(min_length=1)
    node: Literal["tavily_web", "govfiles_registry"]
    query: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)
    search_plan_index: int = Field(ge=0)


class RegistryOfficer(BaseModel):
    name: str = Field(min_length=1)
    title: str | None = None
    role: str | None = None
    address: str | None = None


class RegistryProfile(BaseModel):
    """Structured registry enrichment plus public evidence status."""

    status: str | None = None
    status_raw: str | None = None
    legal_form: str | None = None
    legal_form_raw: str | None = None
    formed_on: date | None = None
    officers: list[RegistryOfficer] = Field(default_factory=list)
    registered_address: str | None = None
    mailing_address: str | None = None
    names: list[str] = Field(default_factory=list)
    filings: list[dict[str, object]] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    county: County | None = None
    sector: Sector | None = None
    public_entity_url: str | None = None
    public_detail_url: str | None = None
    govfiles_detail_url: str | None = None
    evidence_status: Literal[
        "public_detail",
        "search_page_only",
        "blocked",
        "rate_limited",
        "http_error",
        "network_error",
        "low_quality",
        "unsupported_source",
        "parse_failed",
        "failed",
        "none",
    ] = "none"
    evidence_reason: str | None = None
    evidence_source: str | None = None
    evidence_http_status: int | None = None
    evidence_backend: str | None = None
    evidence_checked_at: datetime | None = None
    source_method: str = "govfiles_detail"
    fetched_at: datetime = Field(default_factory=_now)


class ResolvedEntity(BaseModel):
    """A company identity resolved from a web, directory, or registry candidate."""

    company_name: str = Field(min_length=1)
    official_url: str | None = None
    root_domain: str | None = None
    method: Literal["web", "registry", "json_ld", "metadata", "title", "llm", "search"] = "web"
    confidence: Confidence = Confidence.LOW
    registry_ids: list[str] = Field(default_factory=list)
    registry_profile: RegistryProfile | None = None
    sources: list[Candidate] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)

    @field_validator("official_url")
    @classmethod
    def _validate_official_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(TypeAdapter(HttpUrl).validate_python(value))


class CompanyCandidate(BaseModel):
    """A company-level deduplicated result."""

    company_key: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    root_domain: str | None = None
    official_url: str | None = None
    official_url_method: str | None = None
    registry_ids: list[str] = Field(default_factory=list)
    registry_profile: RegistryProfile | None = None
    sources: list[Candidate] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    review_flags: list[str] = Field(default_factory=list)

    @field_validator("official_url")
    @classmethod
    def _validate_official_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(TypeAdapter(HttpUrl).validate_python(value))


class UsageRecord(BaseModel):
    """One metered provider call."""

    run_id: str
    provider: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    calls: int = Field(default=1, ge=0)
    credits: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    detail: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
