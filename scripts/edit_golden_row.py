from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from acquiscout.eval.golden import parse_sheet_row

XLSX_PATH = Path("data/raw/golden_v1.xlsx")
SHEET = "Data"
REPORT_FIELDS = (
    "also_serves_counties",
    "contact_value",
    "contact_checked_at",
    "human_verdict",
    "rejection_reason",
    "score_ownership",
    "score_market",
    "human_score_total",
)


def load_record(path: str | Path) -> dict[str, object]:
    with Path(path).open(encoding="utf-8") as handle:
        record = json.load(handle)
    if not isinstance(record, dict):
        raise ValueError("Record JSON must be an object")
    return record


def write_value(value: object) -> object:
    if isinstance(value, str) and value == "":
        return None
    return value


def edit_row(
    path: str | Path, sheet_name: str, record: dict[str, object]
) -> tuple[int, dict, dict]:
    path = Path(path)
    workbook = load_workbook(path)
    sheet = workbook[sheet_name]

    headers = [cell.value for cell in sheet[1]]
    if any(header is None for header in headers):
        raise ValueError("Header row contains blank cells")

    unknown = set(record) - set(headers)
    if unknown:
        raise ValueError(f"Unknown columns: {sorted(unknown)}")

    company_id = record.get("company_id")
    if not isinstance(company_id, str) or not company_id:
        raise ValueError("company_id is required")

    matches = [row for row in range(2, sheet.max_row + 1) if sheet.cell(row, 1).value == company_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one row for {company_id}, found {len(matches)}")
    row = matches[0]

    columns = {header: index for index, header in enumerate(headers, start=1)}
    before = {key: sheet.cell(row, columns[key]).value for key in REPORT_FIELDS}

    for key, value in record.items():
        sheet.cell(row, columns[key]).value = write_value(value)

    row_values = {
        header: sheet.cell(row, index).value for index, header in enumerate(headers, start=1)
    }
    parse_sheet_row(row_values)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    workbook.save(path)

    after = {key: sheet.cell(row, columns[key]).value for key in REPORT_FIELDS}
    return row, before, after


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Edit one golden workbook row")
    parser.add_argument("record_json")
    parser.add_argument("--xlsx", type=Path, default=XLSX_PATH)
    parser.add_argument("--sheet", default=SHEET)
    args = parser.parse_args(argv)
    record = load_record(args.record_json)
    row, before, after = edit_row(args.xlsx, args.sheet, record)
    print(f"Edited {record['company_id']} at row {row}")
    for key in REPORT_FIELDS:
        print(f"  {key}: {before[key]!r} -> {after[key]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
