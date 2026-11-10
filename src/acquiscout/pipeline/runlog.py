"""Append one Markdown row per discovery run."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acquiscout.config import ROOT, Settings

HEADER = (
    "# Discovery Runs Log\n\n"
    "Auto-written by `python -m acquiscout.pipeline`; one row per run. "
    "The CLI upserts by `run_id`; do not fabricate entries.\n\n"
    "| run_id | finished_at | status | plan | failures | candidates | "
    "provider_errors | abort_reason | checkpoint |\n"
    "|---|---|---|---:|---:|---:|---:|---|---|\n"
)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _row(final: Mapping[str, Any], settings: Settings) -> str:
    return (
        "| "
        + " | ".join(
            [
                _cell(final.get("run_id", "")),
                _cell(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
                _cell(final.get("run_status", "")),
                _cell(len(final.get("search_plan") or [])),
                _cell(final.get("search_failure_count", 0)),
                _cell(len(final.get("deduplicated_candidates") or [])),
                _cell(len(final.get("provider_errors") or [])),
                _cell(final.get("abort_reason") or ""),
                _cell(_display_path(settings.checkpoint_db_path)),
            ]
        )
        + " |"
    )


def append_discovery_run(
    final: Mapping[str, Any], settings: Settings, path: str | Path | None = None
) -> Path:
    target = Path(path or settings.discovery_run_log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(HEADER, encoding="utf-8")

    lines = target.read_text(encoding="utf-8").splitlines()
    run_id = str(final.get("run_id", ""))
    prefix = f"| {_cell(run_id)} |"
    new_row = _row(final, settings)
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = new_row
            replaced = True
            break
    if not replaced:
        lines.append(new_row)

    handle, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            temp.write("\n".join(lines) + "\n")
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


__all__ = ["HEADER", "append_discovery_run"]
