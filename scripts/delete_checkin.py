# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
delete_checkin.py — Remove one or more check-ins from checkins.csv and D1.

Usage:
  python scripts/delete_checkin.py \\
    --ids CHECKIN_ID1,CHECKIN_ID2 \\
    --csv path/to/checkins.csv \\
    --photos path/to/photos.json

  # Clean up photos.json keys left behind by earlier deletions:
  python scripts/delete_checkin.py --prune-orphans \\
    --csv path/to/checkins.csv --photos path/to/photos.json

With --photos, the deleted check-ins' photos.json entries are removed too, and
the now-unreferenced image filenames are reported so the caller can delete them
from R2 (photos.json is the only index — a stale key renders as a blank card).

D1 credentials from environment:
  CF_D1_TOKEN, CF_ACCOUNT_ID (optional), CF_D1_DATABASE_ID (optional)

Outputs (for GitHub Actions via GITHUB_OUTPUT):
  DELETED=true/false
  COUNT=N
  PHOTOS_CHANGED=true/false — photos.json was rewritten
  PHOTO_COUNT=N             — image files no longer referenced by anything
  PHOTO_FILES=a.jpg b.jpg   — space-separated, for `aws s3 rm`
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import d1_client

# Audit table for every D1 deletion (mirrors sync_to_d1.SQL_VENUE_CHANGES).
SQL_VENUE_CHANGES = (
    "INSERT OR REPLACE INTO venue_changes "
    "(venue_id,field,old_value,new_value,detected_at,venue_name,action) "
    "VALUES (?,?,?,?,?,?,?)"
)


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


def _prune_photos(photos_path: str, dead_ids: set[str],
                  dry_run: bool) -> tuple[list[str], int]:
    """Drop `dead_ids` from photos.json. Returns (freed_filenames, keys_dropped).

    `freed_filenames` are the images no longer referenced by ANY check-in — the
    only ones safe to remove from R2. A photo inherited by a re-created check-in
    stays out of that list even though its old key is dropped.

    photos.json ({checkin_id: [filename, ...]}) is the ONLY index of what's in
    R2, so a key left behind by a deleted check-in keeps its image alive on the
    photos page with no venue/date. Empty lists are fetch_photos.py's
    "already probed, no photos" markers — dropping them for a dead check-in is
    correct, it will never be probed again.
    """
    p = Path(photos_path)
    if not p.exists():
        print(f"WARNING: photos file not found: {photos_path}")
        return [], 0

    photos = json.loads(p.read_text(encoding="utf-8"))
    hit = {cid: photos[cid] for cid in dead_ids if cid in photos}
    if not hit:
        return [], 0

    # A filename could in principle be listed under a surviving check-in too;
    # only report the ones nothing references anymore.
    survivors = {f for cid, files in photos.items() if cid not in hit for f in files}
    freed = sorted({f for files in hit.values() for f in files} - survivors)

    for cid in hit:
        del photos[cid]
    if dry_run:
        print(f"[dry-run] would drop {len(hit)} photos.json key(s), "
              f"freeing {len(freed)} image(s)")
        return freed, len(hit)

    p.write_text(json.dumps(photos, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    print(f"photos.json: dropped {len(hit)} key(s) → {len(photos)} remain")
    for f in freed:
        print(f"  unreferenced image: {f}")
    return freed, len(hit)


def _emit(key: str, value: str) -> None:
    gho = os.environ.get("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete check-in(s) from CSV and D1")
    ap.add_argument("--ids",
                    help="Comma-separated checkin_id(s) to delete")
    ap.add_argument("--csv", required=True,
                    help="Path to checkins.csv")
    ap.add_argument("--photos",
                    help="Path to photos.json — prune the deleted check-ins' "
                         "photo entries and report the freed image filenames")
    ap.add_argument("--prune-orphans", action="store_true",
                    help="With --photos: also drop photos.json keys whose "
                         "check-in is already absent from the CSV")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be deleted without making changes")
    args = ap.parse_args()

    if not args.ids and not args.prune_orphans:
        sys.exit("Nothing to do: pass --ids and/or --prune-orphans.")
    if args.prune_orphans and not args.photos:
        sys.exit("--prune-orphans requires --photos.")

    def _extract_id(raw: str) -> str:
        # Accept full Swarm URLs: https://www.swarmapp.com/user/.../checkin/<id>
        raw = raw.strip()
        if "/" in raw:
            raw = raw.rstrip("/").rsplit("/", 1)[-1]
        return raw

    ids_to_delete = {_extract_id(i) for i in (args.ids or "").split(",") if i.strip()}
    if not ids_to_delete and not args.prune_orphans:
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

    # Photo keys to drop: those of the check-ins being deleted, plus (opt-in) any
    # already stranded by an earlier deletion that predates --photos support.
    photo_ids = set(ids_to_delete)
    if args.prune_orphans:
        live = {r["checkin_id"] for r in kept}
        stale = {cid for cid in json.loads(Path(args.photos).read_text(encoding="utf-8"))
                 if cid not in live}
        if stale:
            print(f"Orphaned photos.json keys (check-in already gone): {len(stale)}")
        photo_ids |= stale

    freed: list[str] = []
    dropped = 0
    if args.photos and photo_ids:
        freed, dropped = _prune_photos(args.photos, photo_ids, args.dry_run)
    _emit("PHOTOS_CHANGED", "true" if dropped and not args.dry_run else "false")
    _emit("PHOTO_COUNT", str(len(freed)))
    _emit("PHOTO_FILES", " ".join(freed))

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
        now_ts = int(time.time())

        d1_client.query(
            f"DELETE FROM checkins WHERE id IN ({placeholders})",
            id_list,
        )
        print(f"D1: removed {len(id_list)} row(s) from checkins")

        # Audit each removed check-in (subject = checkin id → unique PK;
        # old_value carries the venue_id it belonged to).
        audit_rows = [
            [r["checkin_id"], "checkin", r.get("venue_id") or None,
             None, now_ts, r.get("venue") or None, "deleted"]
            for r in deleted
        ]

        # Update or remove venues affected by the deletion
        deleted_venue_ids = {r["venue_id"] for r in deleted if r.get("venue_id")}
        kept_venue_ids    = {r["venue_id"] for r in kept    if r.get("venue_id")}
        orphans  = deleted_venue_ids - kept_venue_ids
        affected = deleted_venue_ids - orphans  # still have remaining check-ins

        # Names for orphaned venues, taken from the deleted CSV rows.
        orphan_names = {r["venue_id"]: r.get("venue") for r in deleted if r.get("venue_id")}
        if orphans:
            ph = ",".join("?" * len(orphans))
            d1_client.query(
                f"DELETE FROM venues WHERE id IN ({ph})",
                list(orphans),
            )
            print(f"D1: removed {len(orphans)} orphaned venue(s) from venues")
            audit_rows += [
                [vid, "venue", orphan_names.get(vid), None, now_ts,
                 orphan_names.get(vid), "deleted"]
                for vid in orphans
            ]

        if audit_rows:
            d1_client.batch_upsert(SQL_VENUE_CHANGES, audit_rows,
                                   label="venue_changes(deleted)")

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
