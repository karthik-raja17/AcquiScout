"""Deterministic dev fixtures for search and planner calls.

Fixtures are per-developer and gitignored. Capture happens at the provider
wrapper, which is the only lossless source of truth.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acquiscout.sources.cache import cache_key


class FixtureMissing(RuntimeError):
    def __init__(self, kind: str, provider: str, key: str, path: Path) -> None:
        super().__init__(
            f"No frozen fixture for {kind}/{provider}/{key} at {path}. "
            "Run once with ACQUISCOUT_CAPTURE_SEARCH=1 or "
            "ACQUISCOUT_CAPTURE_PLANNER=1 first."
        )
        self.kind = kind
        self.provider = provider
        self.key = key
        self.path = path


def fixture_key(provider: str, query: str, params: dict[str, object], version: str) -> str:
    return cache_key(provider, query, params, version)


def fixture_path(root: str | Path, kind: str, provider: str, key: str) -> Path:
    return Path(root) / kind / provider / f"{key}.json"


def save_fixture(
    root: str | Path,
    kind: str,
    provider: str,
    key: str,
    payload: Any,
) -> Path:
    path = fixture_path(root, kind, provider, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": kind,
        "provider": provider,
        "key": key,
        "captured_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            json.dump(document, temp, indent=2, default=str)
            temp.write("\n")
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


def load_fixture(root: str | Path, kind: str, provider: str, key: str) -> Any:
    path = fixture_path(root, kind, provider, key)
    if not path.exists():
        raise FixtureMissing(kind, provider, key, path)
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    return document["payload"]


__all__ = [
    "FixtureMissing",
    "fixture_key",
    "fixture_path",
    "load_fixture",
    "save_fixture",
]
