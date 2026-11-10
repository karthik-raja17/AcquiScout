from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from acquiscout.eval.golden import COMPONENT_CAPS


def load_records(path: str | Path) -> list[dict[str, object]]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError("Record JSON must be an object or an array of objects")
    if not records:
        raise ValueError("Record JSON is empty")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} is not an object")
    return records


def validate_scores(record: dict[str, object]) -> None:
    for field, cap in COMPONENT_CAPS.items():
        if field not in record:
            raise ValueError(f"Missing score field: {field}")
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        if not 0 <= value <= cap:
            raise ValueError(f"{field}={value} is outside 0-{cap}")
    components = sum(int(record[field]) for field in COMPONENT_CAPS)
    total = record.get("human_score_total")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        raise ValueError("human_score_total must be numeric")
    if components != total:
        raise ValueError(f"Components sum to {components}, human_score_total is {total}")


def append_rows(
    path: str | Path, sheet_name: str, records: list[dict[str, object]]
) -> tuple[list[tuple[str, int]], int]:
    for record in records:
        validate_scores(record)

    path = Path(path)
    wb = load_workbook(path)
    ws = wb[sheet_name]

    headers = [cell.value for cell in ws[1]]
    if any(header is None for header in headers):
        raise ValueError("Header row contains blank cells")

    existing = {
        ws.cell(row, 1).value
        for row in range(2, ws.max_row + 1)
        if ws.cell(row, 1).value is not None
    }
    batch: set[object] = set()
    for record in records:
        missing = set(headers) - set(record)
        extra = set(record) - set(headers)
        if missing or extra:
            raise ValueError(
                f"Record keys do not match headers: missing={sorted(missing)} extra={sorted(extra)}"
            )
        company_id = record["company_id"]
        if not isinstance(company_id, str) or not company_id:
            raise ValueError("company_id must be a non-empty string")
        if company_id in existing:
            raise ValueError(f"{company_id} already present in {sheet_name}")
        if company_id in batch:
            raise ValueError(f"{company_id} appears more than once in the batch")
        batch.add(company_id)

    target = 2
    while target <= ws.max_row and ws.cell(target, 1).value is not None:
        target += 1

    written: list[tuple[str, int]] = []
    for record in records:
        for column, header in enumerate(headers, start=1):
            if header == "rejection_reason" and not record[header]:
                continue
            ws.cell(target, column).value = record[header]
        written.append((str(record["company_id"]), target))
        target += 1

    total = sum(1 for row in range(2, ws.max_row + 1) if ws.cell(row, 1).value is not None)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    wb.save(path)
    return written, total


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Append golden records to a workbook")
    parser.add_argument("records_json")
    parser.add_argument("--xlsx", type=Path, default=Path("data/raw/golden_v1.xlsx"))
    parser.add_argument("--sheet", default="Data")
    args = parser.parse_args(argv)
    records = load_records(args.records_json)
    written, total = append_rows(args.xlsx, args.sheet, records)
    if len(written) == 1:
        company_id, row = written[0]
        print(f"Appended {company_id} at row {row}; {total} records in Data")
    else:
        for company_id, row in written:
            print(f"Appended {company_id} at row {row}")
        print(f"{total} records in Data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
