# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
delete_checkin.py — Remove one or more check-ins from checkins.csv and D1.

Usage:
  python scripts/delete_checkin.py \\
    --ids CHECKIN_ID1,CHECKIN_ID2 \\
    --csv path/to/checkins.csv

D1 credentials from environment:
  CF_D1_TOKEN, CF_ACCOUNT_ID (optional), CF_D1_DATABASE_ID (optional)

Outputs (for GitHub Actions via GITHUB_OUTPUT):
  DELETED=true/false
  COUNT=N
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import d1_client


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _emit(key: str, value: str) -> None:
    gho = os.environ.get("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete check-in(s) from CSV and D1")
    ap.add_argument("--ids", required=True,
                    help="Comma-separated checkin_id(s) to delete")
    ap.add_argument("--csv", required=True,
                    help="Path to checkins.csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be deleted without making changes")
    args = ap.parse_args()

    ids_to_delete = {i.strip() for i in args.ids.split(",") if i.strip()}
    if not ids_to_delete:
        sys.exit("No IDs provided.")

    token = os.environ.get("CF_D1_TOKEN", "")
    if token:
        d1_client.configure(token)

    rows = _load(args.csv)
    deleted = [r for r in rows if r["checkin_id"] in ids_to_delete]
    kept    = [r for r in rows if r["checkin_id"] not in ids_to_delete]

    missing = ids_to_delete - {r["checkin_id"] for r in deleted}
    if missing:
        print(f"WARNING: not found in CSV: {', '.join(sorted(missing))}")

    if not deleted:
        print("Nothing to delete.")
        _emit("DELETED", "false")
        _emit("COUNT", "0")
        return

    print(f"Check-ins to delete ({len(deleted)}):")
    for r in deleted:
        print(f"  {r['checkin_id']}  {r['date']}  {r.get('venue', '')}  {r.get('city', '')}")

    if args.dry_run:
        print("[dry-run] No changes made.")
        _emit("DELETED", "false")
        _emit("COUNT", "0")
        return

    # Update CSV
    _save(args.csv, kept)
    print(f"CSV: {len(rows)} → {len(kept)} rows")

    # D1
    if not token:
        print("CF_D1_TOKEN not set — skipping D1 delete.")
    else:
        id_list = list(ids_to_delete)
        placeholders = ",".join("?" * len(id_list))

        d1_client.query(
            f"DELETE FROM checkins WHERE checkin_id IN ({placeholders})",
            id_list,
        )
        print(f"D1: removed {len(id_list)} row(s) from checkins")

        # Update or remove venues affected by the deletion
        deleted_venue_ids = {r["venue_id"] for r in deleted if r.get("venue_id")}
        kept_venue_ids    = {r["venue_id"] for r in kept    if r.get("venue_id")}
        orphans  = deleted_venue_ids - kept_venue_ids
        affected = deleted_venue_ids - orphans  # still have remaining check-ins

        if orphans:
            ph = ",".join("?" * len(orphans))
            d1_client.query(
                f"DELETE FROM venues WHERE id IN ({ph})",
                list(orphans),
            )
            print(f"D1: removed {len(orphans)} orphaned venue(s) from venues")

        for vid in affected:
            d1_client.query(
                "UPDATE venues SET "
                "first_checkin_at = (SELECT MIN(date) FROM checkins WHERE venue_id = ?), "
                "last_checkin_at  = (SELECT MAX(date) FROM checkins WHERE venue_id = ?), "
                "checkin_count    = (SELECT COUNT(*)  FROM checkins WHERE venue_id = ?) "
                "WHERE id = ?",
                [vid, vid, vid, vid],
            )
        if affected:
            print(f"D1: updated timestamps/count for {len(affected)} venue(s)")

    _emit("DELETED", "true")
    _emit("COUNT", str(len(deleted)))
    print("Done.")


if __name__ == "__main__":
    main()
