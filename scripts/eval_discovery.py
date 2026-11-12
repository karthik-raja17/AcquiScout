from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from acquiscout.config import get_settings
from acquiscout.eval.discovery import (
    evaluate_samples,
    format_report,
    load_samples,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate discovery crawl-worthiness")
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("data/raw/discovery_eval_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/generated/discovery_eval"),
    )
    parser.add_argument("--min-chars", type=int, default=400)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    samples = load_samples(args.samples)
    settings = get_settings()
    metrics, checks = await evaluate_samples(samples, settings, min_chars=args.min_chars)
    print(format_report(metrics))

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = args.output / f"discovery_eval_{stamp}.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": "discovery_eval_v1",
                "evaluated_at": datetime.now(UTC).isoformat(),
                "samples_file": str(args.samples),
                "metrics": metrics.model_dump(mode="json"),
                "checks": [check.model_dump(mode="json") for check in checks],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote={target}")
    if args.strict and not all(metrics.gates.values()):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    raise SystemExit(main())
