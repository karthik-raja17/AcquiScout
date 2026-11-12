from __future__ import annotations

import json
from pathlib import Path

import pytest

from acquiscout.sources.archive import SCHEMA_VERSION, RunArchive


@pytest.mark.asyncio
async def test_run_archive_writes_all_artifacts(tmp_path: Path) -> None:
    archive = RunArchive(tmp_path, "run/with spaces")

    search_path = await archive.write_search(
        "tavily_web",
        "task 1",
        {"results": [{"url": "https://example.com"}], "error": None},
    )
    plan_path = await archive.write_plan({"search_plan": []})
    manifest_path = await archive.write_manifest({"run_status": "completed"})
    usage_path = await archive.write_usage({"totals": {"tavily_credits": 1}})

    assert search_path is not None
    assert plan_path is not None
    assert manifest_path is not None
    assert usage_path is not None
    assert search_path.exists()
    assert plan_path.exists()
    assert manifest_path.exists()
    assert usage_path.exists()
    assert "run_with_spaces" in str(archive.directory)
    assert archive.search_files == 1

    search = json.loads(search_path.read_text(encoding="utf-8"))
    assert search["schema_version"] == SCHEMA_VERSION
    assert search["run_id"] == "run/with spaces"
    assert search["results"][0]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_run_archive_write_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    archive = RunArchive(tmp_path, "run")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(archive, "_write", fail)

    assert await archive.write_plan({"search_plan": []}) is None
