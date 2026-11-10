"""Failure queue for search providers. Redis first, JSONL fallback, never raises."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DLQ_KEY = "dlq:search"
DLQ_DEAD_KEY = "dlq:search:dead"
DLQ_MAX_LENGTH = 10_000


def _entry(entry: dict[str, Any]) -> dict[str, Any]:
    document = dict(entry)
    document.setdefault("created_at", datetime.now(UTC).isoformat())
    return document


def _write_jsonl(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")


async def push_failure(
    entry: dict[str, Any],
    *,
    redis_client: Any | None = None,
    fallback_path: Path | None = None,
) -> bool:
    """Record a failure. Returns True when at least one sink accepted it."""
    payload = json.dumps(_entry(entry), default=str)

    if redis_client is not None:
        try:
            await redis_client.lpush(DLQ_KEY, payload)
            await redis_client.ltrim(DLQ_KEY, 0, DLQ_MAX_LENGTH - 1)
            return True
        except Exception as exc:  # pragma: no cover - depends on external service
            logger.warning(
                "DLQ Redis write failed for %s (%s); falling back to JSONL",
                entry.get("provider", "unknown"),
                type(exc).__name__,
            )

    if fallback_path is not None:
        try:
            _write_jsonl(Path(fallback_path), payload)
            return True
        except Exception as exc:
            logger.error(
                "DLQ write failed for %s/%s on both sinks: %s",
                entry.get("provider", "unknown"),
                entry.get("query", "")[:80],
                type(exc).__name__,
            )
            return False

    logger.error(
        "DLQ entry dropped: no Redis client and no fallback path (%s)",
        entry.get("provider", "unknown"),
    )
    return False


__all__ = ["DLQ_DEAD_KEY", "DLQ_KEY", "DLQ_MAX_LENGTH", "push_failure"]
