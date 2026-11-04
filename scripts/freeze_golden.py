"""One-way freeze of golden_v1.json. Refuses to overwrite an existing frozen file.

Per EVALUATION.md §1: never mutate v1 in place. Use golden_v2.json for expansions.

Usage:
    python scripts/freeze_golden.py

Preconditions:
    - data/raw/golden_v1.xlsx exists with the 19 records
    - data/raw/golden_v1.json does NOT exist (if it does, this script errors)
    - scripts/export_golden.py can produce a validated 19-record export

Outputs:
    - data/raw/golden_v1.json (frozen, with header metadata)
    - data/raw/golden_v1.json.sha256 (sidecar hash)
    - prints SHA-256 and record counts
"""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

target = Path("data/raw/golden_v1.json")
if target.exists():
    print(f"ERROR: {target} already exists. golden_v1 is frozen.")
    print("Create golden_v2.json for expansions per ADR-026.")
    sys.exit(1)

result = subprocess.run(
    [sys.executable, "scripts/export_golden.py"],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(f"Export failed:\n{result.stderr}")
    sys.exit(1)

smoke_path = Path("data/raw/golden_smoke.json")
if not smoke_path.exists():
    print(f"ERROR: {smoke_path} not found after export")
    sys.exit(1)

exported = json.loads(smoke_path.read_text(encoding="utf-8"))
records = exported["records"] if "records" in exported else exported

unique_entities = len({record["true_entity_id"] for record in records})
verdict_counts: dict[str, int] = {}
for record in records:
    verdict = record["human_verdict"]
    verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

frozen = {
    "schema_version": "1.0",
    "market_id": "tampa_bay",
    "unit": "observed_company_mention",
    "status": "frozen",
    "frozen_at": str(date.today()),
    "record_count": len(records),
    "unique_entity_count": unique_entities,
    "verdict_distribution": verdict_counts,
    "records": records,
}

target.write_text(json.dumps(frozen, indent=2, ensure_ascii=False), encoding="utf-8")

digest = hashlib.sha256(target.read_bytes()).hexdigest()
hash_path = Path("data/raw/golden_v1.json.sha256")
hash_path.write_text(f"{digest}  {target}\n", encoding="utf-8")

print(f"Frozen: {target}")
print(f"Records: {len(records)}")
print(f"Unique entities: {unique_entities}")
print(f"Verdict distribution: {verdict_counts}")
print(f"SHA-256: {digest}")
