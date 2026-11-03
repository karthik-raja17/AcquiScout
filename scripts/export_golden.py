from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from pydantic import ValidationError

from acquiscout.eval.golden import GoldenRecord, parse_sheet_row

XLSX_PATH = Path("data/raw/golden_v1.xlsx")
OUTPUT_PATH = Path("data/raw/golden_smoke.json")


def read_metadata(sheet) -> dict[str, str]:
    metadata = {}
    for label, value in sheet.iter_rows(min_row=1, min_col=2, max_col=3, values_only=True):
        if label and value is not None:
            metadata[str(label).strip()] = str(value).strip()
    return metadata


def iter_sheet_rows(sheet):
    headers = [cell.value for cell in sheet[1]]
    if any(header is None for header in headers):
        raise ValueError("Data header row contains blank cells")
    row = 2
    while sheet.cell(row, 1).value is not None:
        yield {
            str(header): sheet.cell(row, column).value
            for column, header in enumerate(headers, start=1)
        }
        row += 1


def describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)


def main() -> int:
    workbook = load_workbook(XLSX_PATH)
    metadata = read_metadata(workbook["Metadata"])
    missing = {"market_id", "unit"} - set(metadata)
    if missing:
        print(f"Metadata sheet is missing: {sorted(missing)}", file=sys.stderr)
        return 1

    records: list[GoldenRecord] = []
    errors: list[tuple[object, Exception]] = []
    seen: set[str] = set()

    for raw in iter_sheet_rows(workbook["Data"]):
        company_id = raw.get("company_id")
        try:
            record = parse_sheet_row(raw)
        except (ValidationError, ValueError) as exc:
            errors.append((company_id, exc))
            continue
        if record.company_id in seen:
            errors.append((company_id, ValueError("duplicate company_id")))
            continue
        seen.add(record.company_id)
        records.append(record)

    if errors:
        for company_id, exc in errors:
            print(f"{company_id}: {describe(exc)}", file=sys.stderr)
        print(f"{len(errors)} record(s) failed; no JSON written", file=sys.stderr)
        return 1

    payload = {
        "schema_version": "1.0-pilot",
        "market_id": metadata["market_id"],
        "unit": metadata["unit"],
        "status": "pilot",
        "exported_at": date.today().isoformat(),
        "records": [record.model_dump(mode="json") for record in records],
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} records to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
