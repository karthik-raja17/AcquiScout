"""Lossless per-run discovery archive.

Every search node writes its own file before returning, so a crash, abort, or
bouncer clear cannot lose the raw results.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "discovery_archive_v1"


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unknown"


class RunArchive:
    def __init__(self, root: str | Path, run_id: str) -> None:
        self._run_id = run_id
        self._dir = Path(root) / _safe(run_id)
        self._search_files = 0

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def search_files(self) -> int:
        return self._search_files

    def _write(self, relative: str, payload: dict[str, Any]) -> Path:
        target = self._dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema_version": SCHEMA_VERSION, "run_id": self._run_id, **payload}
        handle, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temp:
                json.dump(document, temp, indent=2, default=str)
                temp.write("\n")
            os.replace(temp_name, target)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return target

    async def write_json(self, relative: str, payload: dict[str, Any]) -> Path | None:
        try:
            return await asyncio.to_thread(self._write, relative, payload)
        except Exception as exc:
            logger.warning("discovery archive write failed (%s): %s", relative, type(exc).__name__)
            return None

    async def write_search(
        self,
        provider: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> Path | None:
        path = await self.write_json(f"raw/{_safe(provider)}/{_safe(task_id)}.json", payload)
        if path is not None:
            self._search_files += 1
        return path

    async def write_directory(self, identifier: str, payload: dict[str, Any]) -> Path | None:
        digest = hashlib.sha256(identifier.encode()).hexdigest()[:24]
        return await self.write_json(f"directory/{digest}.json", payload)

    async def write_registry(self, identifier: str, payload: dict[str, Any]) -> Path | None:
        digest = hashlib.sha256(identifier.encode()).hexdigest()[:24]
        return await self.write_json(f"registry/{digest}.json", payload)

    async def write_registry_evidence(
        self, identifier: str, payload: dict[str, Any]
    ) -> Path | None:
        digest = hashlib.sha256(identifier.encode()).hexdigest()[:24]
        return await self.write_json(f"registry_evidence/{digest}.json", payload)

    async def write_company_url(self, identifier: str, payload: dict[str, Any]) -> Path | None:
        digest = hashlib.sha256(identifier.encode()).hexdigest()[:24]
        return await self.write_json(f"company_url/{digest}.json", payload)

    async def write_dropped(self, payload: dict[str, Any]) -> Path | None:
        return await self.write_json("dropped.json", payload)

    async def write_plan(self, payload: dict[str, Any]) -> Path | None:
        return await self.write_json("plan.json", payload)

    async def write_manifest(self, payload: dict[str, Any]) -> Path | None:
        return await self.write_json("manifest.json", payload)

    async def write_usage(self, payload: dict[str, Any]) -> Path | None:
        return await self.write_json("usage.json", payload)


__all__ = ["RunArchive", "SCHEMA_VERSION"]
