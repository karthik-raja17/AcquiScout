import os

import pytest

from acquiscout.config import Settings
from acquiscout.observability import configure_langsmith, tracing_metadata


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_configure_langsmith_enables_both_env_names() -> None:
    settings = make_settings(
        langsmith_tracing=True,
        langsmith_api_key="test-key",
        langsmith_project="acquiscout-test",
    )

    configure_langsmith(settings)

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "test-key"
    assert os.environ["LANGCHAIN_API_KEY"] == "test-key"
    assert os.environ["LANGSMITH_PROJECT"] == "acquiscout-test"
    assert os.environ["LANGCHAIN_PROJECT"] == "acquiscout-test"


def test_configure_langsmith_explicitly_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    settings = make_settings(langsmith_tracing=False)

    configure_langsmith(settings)

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_tracing_metadata_uses_agent_settings() -> None:
    settings = make_settings(
        langsmith_agent_id="research-agent",
        langsmith_agent_environment="local",
    )

    assert tracing_metadata(settings) == {
        "agent_id": "research-agent",
        "agent_environment": "local",
    }
