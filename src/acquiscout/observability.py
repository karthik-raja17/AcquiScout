"""Bridge application settings to the LangSmith tracing environment."""

from __future__ import annotations

import os

from acquiscout.config import Settings


def configure_langsmith(settings: Settings) -> None:
    """Enable or explicitly disable LangSmith tracing for this process.

    LangGraph/LangChain read tracing configuration from environment variables,
    so settings loaded from `.env` must be copied across before the graph runs.
    """
    if not settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return

    if settings.langsmith_api_key is None:
        raise RuntimeError("LangSmith tracing requested without LANGSMITH_API_KEY")

    key = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = key
    os.environ["LANGCHAIN_API_KEY"] = key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint


def tracing_metadata(settings: Settings) -> dict[str, str]:
    return {
        "agent_id": settings.langsmith_agent_id,
        "agent_environment": settings.langsmith_agent_environment,
    }


__all__ = ["configure_langsmith", "tracing_metadata"]
