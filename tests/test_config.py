import pytest
from pydantic import ValidationError

from acquiscout.config import Settings
from acquiscout.schemas import County, ScoreWeights, Sector


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_v1_defaults() -> None:
    settings = _settings()
    assert set(settings.counties) == {County.HILLSBOROUGH, County.PINELLAS, County.PASCO}
    assert set(settings.sectors) == {Sector.TITLE_COMPANY, Sector.PROPERTY_MANAGEMENT}
    assert sum(settings.weights.model_dump().values()) == 100
    assert settings.min_revenue_usd == 1_500_000
    assert settings.min_ebitda_usd == 500_000
    assert settings.audit_sample_rate == 0.20
    assert settings.enable_directory_resolver is False
    assert settings.scrape_circuit_enabled is True
    assert settings.scrape_circuit_failure_threshold == 1
    assert settings.scrape_circuit_cooldown_seconds == 600


def test_default_funnel_is_monotonic() -> None:
    settings = _settings()
    funnel = [
        settings.weekly_raw,
        settings.weekly_deduped,
        settings.weekly_scraped,
        settings.weekly_qualified,
        settings.weekly_verified,
    ]
    assert funnel == sorted(funnel, reverse=True)


def test_bad_weights_rejected() -> None:
    with pytest.raises(ValidationError):
        ScoreWeights(financial_fit=40)


def test_non_monotonic_funnel_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(weekly_scraped=600)


def test_audit_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        _settings(audit_sample_rate=0)
    with pytest.raises(ValidationError):
        _settings(audit_sample_rate=1.5)


def test_empty_market_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(counties=[])


def test_acquiscout_env_alias_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACQUISCOUT_ENV", "prod")
    assert _settings().app_env == "prod"


def test_prod_rejects_fixture_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACQUISCOUT_ENV", "prod")
    with pytest.raises(ValidationError, match="must be False"):
        _settings(use_fixtures=True)


def test_capture_and_replay_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _settings(capture_search=True, use_fixtures=True)


def test_blank_optional_secrets_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")

    settings = _settings()

    assert settings.jina_api_key is None
    assert settings.firecrawl_api_key is None


def test_langsmith_tracing_requires_key() -> None:
    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY"):
        _settings(langsmith_tracing=True)


def test_langsmith_tracing_accepts_key() -> None:
    settings = _settings(langsmith_tracing=True, langsmith_api_key="test-key")

    assert settings.langsmith_tracing is True
    assert settings.langsmith_project == "acquiscout"
