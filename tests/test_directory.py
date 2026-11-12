from __future__ import annotations

import json

import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.schemas import Candidate
from acquiscout.sources.directory import DirectoryResolver


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def candidate(title: str | None = "Acme Title | LeadIQ") -> Candidate:
    return Candidate(
        identifier="https://leadiq.com/c/acme-title",
        identifier_kind="url",
        source_type="web",
        provider="tavily_web",
        title=title,
    )


def page_client(html: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_json_ld_resolution() -> None:
    html = """
    <html><head>
      <title>Acme Title | LeadIQ</title>
      <script type="application/ld+json">
        {"@type":"Organization","name":"Acme Title","url":"https://acmetitle.com"}
      </script>
    </head><body>Acme Title company profile.</body></html>
    """
    settings = make_settings()
    resolver = DirectoryResolver(settings, http=page_client(html))

    entity = await resolver.resolve(candidate())

    assert entity is not None
    assert entity.company_name == "Acme Title"
    assert entity.official_url == "https://acmetitle.com/"
    assert entity.method == "json_ld"
    assert entity.root_domain == "acmetitle.com"


@pytest.mark.asyncio
async def test_metadata_canonical_resolution() -> None:
    html = """
    <html><head>
      <title>Acme Title</title>
      <meta property="og:title" content="Acme Title | LeadIQ">
      <link rel="canonical" href="https://acmetitle.com/about">
    </head><body>Acme Title company profile.</body></html>
    """
    resolver = DirectoryResolver(make_settings(), http=page_client(html))

    entity = await resolver.resolve(candidate())

    assert entity is not None
    assert entity.company_name == "Acme Title"
    assert entity.official_url == "https://acmetitle.com/about"
    assert entity.method == "metadata"


@pytest.mark.asyncio
async def test_llm_fallback_uses_allowed_link() -> None:
    html = """
    <html><body>
      <a href="https://acmetitle.com">Acme Title</a>
      <p>Acme Title is a title company in Tampa.</p>
    </body></html>
    """
    content = json.dumps(
        {
            "company_name": "Acme Title",
            "official_url": "https://acmetitle.com",
            "confidence": "high",
        }
    )

    def groq_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    groq = httpx.AsyncClient(transport=httpx.MockTransport(groq_handler))
    resolver = DirectoryResolver(
        make_settings(groq_api_key="test-key"),
        http=page_client(html),
        groq_http=groq,
    )

    entity = await resolver.resolve(candidate(None))

    assert entity is not None
    assert entity.company_name == "Acme Title"
    assert entity.official_url == "https://acmetitle.com/"
    assert entity.method == "llm"
    assert entity.confidence.value == "high"
    assert resolver.last_usage is not None
    assert resolver.last_usage["total_tokens"] == 30
