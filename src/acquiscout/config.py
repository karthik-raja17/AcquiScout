"""Application settings. Everything the client can change lives here, not in code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from acquiscout.schemas import County, ScoreWeights, Sector

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(
        default="local",
        validation_alias=AliasChoices("ACQUISCOUT_ENV", "APP_ENV"),
    )
    log_level: str = "INFO"

    capture_search: bool = Field(
        default=False,
        validation_alias=AliasChoices("ACQUISCOUT_CAPTURE_SEARCH", "CAPTURE_SEARCH"),
    )
    capture_planner: bool = Field(
        default=False,
        validation_alias=AliasChoices("ACQUISCOUT_CAPTURE_PLANNER", "CAPTURE_PLANNER"),
    )
    use_fixtures: bool = Field(
        default=False,
        validation_alias=AliasChoices("ACQUISCOUT_USE_FIXTURES", "USE_FIXTURES"),
    )

    counties: list[County] = Field(
        default_factory=lambda: [County.HILLSBOROUGH, County.PINELLAS, County.PASCO]
    )
    sectors: list[Sector] = Field(
        default_factory=lambda: [Sector.TITLE_COMPANY, Sector.PROPERTY_MANAGEMENT]
    )
    weights: ScoreWeights = Field(default_factory=ScoreWeights)

    min_revenue_usd: int = 1_500_000
    min_ebitda_usd: int = 500_000
    # Note: verdicts are gate-based (G1-G5), not score-threshold-based.
    # See EVALUATION.md §3 and ADR-022; do not add a score-based rule.

    weekly_raw: int = 500
    weekly_deduped: int = 150
    weekly_scraped: int = 60
    weekly_qualified: int = 25
    weekly_verified: int = 20

    audit_sample_rate: float = 0.20
    audit_max_critical_error_rate: float = 0.05
    cost_target_per_prospect_usd: float = 5.0

    discovery_cache_ttl_hours: int = 168
    scrape_fallback_to_firecrawl: bool = False
    jina_min_chars: int = 400
    max_tavily_credits_per_run: int = 50
    max_firecrawl_credits_per_run: int = 10
    groq_daily_token_budget: int = 100_000
    gemini_daily_request_budget: int = 1_000

    search_plan_size: int = 4
    search_max_results: int = 10
    failure_abort_ratio: float = 0.5
    max_urls_per_domain: int = 3
    search_prompt_version: str = "planner_v2"
    search_hints_version: str = "search_hints_v1"
    groq_model: str = "openai/gpt-oss-20b"

    api_key: SecretStr = Field(
        default=SecretStr("change-me-local-only"), validation_alias="ACQUISCOUT_API_KEY"
    )
    database_url: str = "postgresql://postgres:postgres@localhost:5432/acquiscout"
    redis_url: str = "redis://localhost:6379/0"

    tavily_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    govfiles_api_key: SecretStr | None = None
    govfiles_base_url: str = ""

    data_raw_dir: Path = ROOT / "data" / "raw"
    data_generated_dir: Path = ROOT / "data" / "generated"
    cache_dir: Path = ROOT / "data" / "cache"
    checkpoint_db_path: Path = ROOT / "data" / "cache" / "checkpoints.db"
    search_cache_db_path: Path = ROOT / "data" / "cache" / "search_cache.db"
    dlq_fallback_path: Path = ROOT / "data" / "dlq" / "search_failures.jsonl"
    dev_fixtures_dir: Path = ROOT / "dev_fixtures"
    discovery_run_log_path: Path = ROOT / "docs" / "discovery-runs.md"

    @field_validator("counties", "sectors")
    @classmethod
    def _non_empty(cls, value: list[County | Sector]) -> list[County | Sector]:
        if not value:
            raise ValueError("at least one value is required")
        return value

    @field_validator("audit_sample_rate", "failure_abort_ratio")
    @classmethod
    def _rate_in_range(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("rate must be in (0, 1]")
        return value

    @field_validator(
        "discovery_cache_ttl_hours",
        "jina_min_chars",
        "max_tavily_credits_per_run",
        "max_firecrawl_credits_per_run",
        "groq_daily_token_budget",
        "gemini_daily_request_budget",
        "search_plan_size",
        "search_max_results",
        "max_urls_per_domain",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("budgets, TTLs, and limits must be positive")
        return value

    @model_validator(mode="after")
    def _funnel_is_monotonic(self) -> Self:
        funnel = [
            self.weekly_raw,
            self.weekly_deduped,
            self.weekly_scraped,
            self.weekly_qualified,
            self.weekly_verified,
        ]
        if any(later > earlier for earlier, later in zip(funnel, funnel[1:], strict=False)):
            raise ValueError("weekly funnel counts must be non-increasing")
        return self

    @model_validator(mode="after")
    def _fixtures_never_in_prod(self) -> Self:
        if self.app_env.lower() in {"prod", "production"} and (
            self.capture_search or self.capture_planner or self.use_fixtures
        ):
            raise ValueError(
                "capture_search, capture_planner, and use_fixtures must be False "
                "when app_env is prod"
            )
        if self.capture_search and self.use_fixtures:
            raise ValueError("capture_search and use_fixtures are mutually exclusive")
        if self.capture_planner and self.use_fixtures:
            raise ValueError("capture_planner and use_fixtures are mutually exclusive")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
