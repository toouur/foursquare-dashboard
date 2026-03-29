# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0
"""
fix_overlap_dupes.py — Remove overlap entries that duplicate with_name/created_by_name.

For each row where overlaps_name contains a name already present in
with_name or created_by_name, remove that name (and matching ID) from
overlaps_name/overlaps_id. If nothing genuine remains, set overlaps_id = "-"
and overlaps_name = "".

Usage:
    python scripts/fix_overlap_dupes.py --csv data/checkins.csv
    python scripts/fix_overlap_dupes.py --csv data/checkins.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


def load_csv(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    return rows, list(fields)


def save_csv(path: Path, rows: list[dict], fields: list[str], retries: int = 10, delay: float = 3.0) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    content = buf.getvalue()
    tmp = path.with_suffix(".tmp")
    for attempt in range(retries):
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
            return
        except OSError as exc:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove duplicate overlap entries")
    parser.add_argument("--csv", default="G:/FoursquareDashboardClaude/local_parsing/checkins.csv",
                        help="Path to checkins.csv")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    rows, fields = load_csv(csv_path)

    fixed = 0
    cleared = 0

    for row in rows:
        overlap_name = row.get("overlaps_name", "").strip()
        overlap_id   = row.get("overlaps_id",   "").strip()

        # Skip rows with no real overlap data
        if not overlap_name or overlap_id in ("", "-", "error"):
            continue

        existing = {
            n.strip().lower()
            for field in ("with_name", "created_by_name")
            for n in row.get(field, "").split(",")
            if n.strip()
        }
        if not existing:
            continue

        names = [n.strip() for n in overlap_name.split(",")]
        ids   = [i.strip() for i in overlap_id.split(",")]

        # Pad ids list in case lengths mismatch
        while len(ids) < len(names):
            ids.append("-")

        pairs = [(n, i) for n, i in zip(names, ids) if n.lower() not in existing]

        if len(pairs) == len(names):
            continue  # nothing to remove

        if pairs:
            new_name = ", ".join(n for n, _ in pairs)
            new_id   = ", ".join(i for _, i in pairs)
        else:
            new_name = ""
            new_id   = "-"
            cleared += 1

        log.info("FIX  [%s] %s | overlap: %r → %r",
                 row.get("checkin_id", ""), row.get("venue", "")[:40],
                 overlap_name, new_name or "(cleared)")

        if not args.dry_run:
            row["overlaps_name"] = new_name
            row["overlaps_id"]   = new_id

        fixed += 1

    log.info("Total fixed: %d (%d cleared to '-')", fixed, cleared)

    if not args.dry_run and fixed:
        save_csv(csv_path, rows, fields)
        log.info("Saved %s", csv_path)
    elif args.dry_run:
        log.info("Dry-run — no changes written.")


if __name__ == "__main__":
    main()
