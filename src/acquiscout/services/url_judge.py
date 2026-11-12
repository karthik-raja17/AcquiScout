"""LLM judge for final discovery URLs.

Manual labels are preferred for baseline URLs. This judge is used only for
newly resolved URLs that have no manual label. It belongs to the experimental
resolver evaluation; see ``docs/resolver-experiment.md``.
"""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum

import httpx
from pydantic import BaseModel, Field, field_validator

from acquiscout.config import Settings
from acquiscout.sources.cache import SQLiteCache

JUDGE_VERSION = "url_judge_v1"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class UrlLabel(StrEnum):
    OFFICIAL_COMPANY = "official_company"
    DIRECTORY = "directory"
    GOVERNMENT = "government"
    NEWS = "news"
    OTHER = "other"


class UrlJudgement(BaseModel):
    label: UrlLabel
    confidence: str = Field(default="low")
    reason: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: object) -> str:
        if isinstance(value, (int, float)):
            if value >= 0.8:
                return "high"
            if value >= 0.5:
                return "medium"
            return "low"
        text = str(value or "low").lower()
        return text if text in {"low", "medium", "high"} else "low"


class JudgeError(RuntimeError):
    pass


class UrlJudge:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        cache: SQLiteCache | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._owns_http = http is None
        self._cache = cache
        self.last_usage: dict[str, object] | None = None

    def _key(self, url: str, title: str | None) -> tuple[str, str, dict[str, object], str]:
        return (
            "url_judge",
            url,
            {"title": title or ""},
            JUDGE_VERSION,
        )

    async def judge(
        self, url: str, title: str | None = None, snippet: str | None = None
    ) -> UrlJudgement:
        self.last_usage = None
        if self._cache is not None:
            cached = await asyncio.to_thread(self._cache.get, *self._key(url, title))
            if isinstance(cached, dict):
                return UrlJudgement.model_validate(cached)

        if self._settings.groq_api_key is None:
            raise JudgeError("GROQ_API_KEY is not configured")
        prompt = (
            "Classify the candidate URL. Return JSON only with keys "
            "`label`, `confidence`, `reason`.\n"
            "`label` must be one of: `official_company`, `directory`, `government`, "
            "`news`, `other`.\n"
            "`official_company` means the official website of one specific company. "
            "Directory, profile, aggregator, marketplace, social, and list pages are "
            "`directory`. Government pages are `government`. News articles are `news`.\n\n"
            f"URL: {url}\n"
            f"Title: {title or ''}\n"
            f"Snippet: {(snippet or '')[:500]}\n"
        )
        body = {
            "model": self._settings.groq_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.groq_api_key.get_secret_value()}"}
        client = self._client()
        last_error: Exception | None = None
        judgement: UrlJudgement | None = None
        for attempt in range(3):
            try:
                response = await client.post(GROQ_ENDPOINT, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
                content = json.loads(payload["choices"][0]["message"]["content"])
                usage = payload.get("usage")
                self.last_usage = usage if isinstance(usage, dict) else None
                judgement = UrlJudgement.model_validate(content)
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
        if judgement is None:
            raise JudgeError(
                f"url judge failed: {type(last_error).__name__}: {last_error}"
            ) from last_error

        if self._cache is not None:
            await asyncio.to_thread(
                self._cache.set,
                *self._key(url, title),
                judgement.model_dump(mode="json"),
            )
        return judgement

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


__all__ = ["JudgeError", "JUDGE_VERSION", "UrlJudge", "UrlJudgement", "UrlLabel"]
