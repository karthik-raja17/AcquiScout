"""SQLite-backed search cache keyed by provider, query, params, and version."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import cast


def canonical_payload(provider: str, query: str, params: dict[str, object], version: str) -> str:
    return json.dumps(
        {"provider": provider, "query": query, "params": params, "version": version},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def cache_key(provider: str, query: str, params: dict[str, object], version: str) -> str:
    return hashlib.sha256(canonical_payload(provider, query, params, version).encode()).hexdigest()


class SQLiteCache:
    """Cache-aside store. A new connection per operation keeps it thread-safe."""

    def __init__(self, db_path: str | Path, ttl_seconds: int) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def get(
        self, provider: str, query: str, params: dict[str, object], version: str
    ) -> object | None:
        key = cache_key(provider, query, params, version)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT value, created_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            value, created_at = row
            if float(created_at) + self._ttl < time.time():
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None
        return cast(object | None, json.loads(value))

    def set(
        self,
        provider: str,
        query: str,
        params: dict[str, object],
        version: str,
        value: object,
    ) -> None:
        key = cache_key(provider, query, params, version)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), time.time()),
            )
            conn.commit()

    def close(self) -> None:
        """Kept for symmetry; connections are short-lived."""


__all__ = ["SQLiteCache", "cache_key", "canonical_payload"]
