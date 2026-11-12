from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from acquiscout.config import Settings
from acquiscout.services.url_judge import JudgeError, UrlJudge, UrlLabel
from acquiscout.sources.cache import SQLiteCache


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "groq_api_key": "test-key"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def client_for(label: str, calls: list[int]) -> httpx.AsyncClient:
    content = json.dumps({"label": label, "confidence": "high", "reason": "test"})

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_url_judge_labels_official_company() -> None:
    calls: list[int] = []
    judge = UrlJudge(make_settings(), http=client_for("official_company", calls))

    result = await judge.judge("https://acmetitle.com", "Acme Title")

    assert result.label is UrlLabel.OFFICIAL_COMPANY
    assert judge.last_usage is not None
    assert judge.last_usage["total_tokens"] == 15
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_url_judge_cache_skips_second_call(tmp_path: Path) -> None:
    calls: list[int] = []
    cache = SQLiteCache(tmp_path / "cache.db", 3600)
    judge = UrlJudge(make_settings(), http=client_for("directory", calls), cache=cache)

    first = await judge.judge("https://leadiq.com/c/acme", "Acme | LeadIQ")
    second = await judge.judge("https://leadiq.com/c/acme", "Acme | LeadIQ")

    assert first.label is UrlLabel.DIRECTORY
    assert second.label is UrlLabel.DIRECTORY
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_url_judge_rejects_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    judge = UrlJudge(
        make_settings(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(JudgeError):
        await judge.judge("https://example.com", "Example")
