"""Export a successful discovery run to JSON."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acquiscout.config import Settings


def export_run(
    final: Mapping[str, Any],
    settings: Settings,
    output_dir: str | Path | None = None,
) -> Path | None:
    if final.get("run_status") != "completed":
        return None
    target_dir = Path(output_dir or (settings.data_generated_dir / "discovery"))
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{final['run_id']}.json"
    payload = {
        "run_id": final["run_id"],
        "exported_at": datetime.now(UTC).isoformat(),
        "status": final["run_status"],
        "target_market": final.get("target_market"),
        "target_sector": final.get("target_sector"),
        "icp": final.get("icp"),
        "search_plan": [task.model_dump(mode="json") for task in final.get("search_plan") or []],
        "search_failure_count": final.get("search_failure_count", 0),
        "provider_errors": final.get("provider_errors") or [],
        "candidates": [
            candidate.model_dump(mode="json")
            for candidate in final.get("deduplicated_candidates") or []
        ],
        "checkpoint": str(settings.checkpoint_db_path),
    }
    handle, temp_name = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            json.dump(payload, temp, indent=2, default=str)
            temp.write("\n")
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


__all__ = ["export_run"]
