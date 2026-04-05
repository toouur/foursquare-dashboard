# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
sync_venue_changes.py — Diff two checkins.csv snapshots, report changed venues,
and patch tips.json with updated venue metadata.

After a full re-fetch, checkins.csv already has fresh venue info from the API.
This script syncs those changes into tips.json without extra API calls.

Usage:
    python scripts/sync_venue_changes.py \\
        --old private-data/archive/checkins_2026-03-26T12-00-00Z.csv \\
        --new private-data/checkins.csv \\
        --tips private-data/tips.json [--dry-run]

Exit codes:
    0 — success (changes applied or nothing to do)
    1 — error (missing files, parse errors)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# Fields compared between snapshots to detect venue changes.
# venue_id is the key; these are the values we track.
TRACKED = ["venue", "city", "country", "lat", "lng", "category"]


def load_csv_by_venue(path: Path) -> dict[str, dict]:
    """
    Return {venue_id: row} using the most-recent check-in per venue
    (highest date value = freshest API data embedded in that row).
    """
    by_venue: dict[str, dict] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            vid = row.get("venue_id", "").strip()
            if not vid:
                continue
            existing = by_venue.get(vid)
            try:
                ts = int(row.get("date") or 0)
            except ValueError:
                ts = 0
            if existing is None or ts > int(existing.get("date") or 0):
                by_venue[vid] = row
    return by_venue


def detect_changes(
    old: dict[str, dict], new: dict[str, dict]
) -> list[dict]:
    """
    Return a list of change records for venue_ids that exist in both snapshots
    but have at least one differing TRACKED field.
    Each record: {venue_id, venue_name, fields: {field: (old_val, new_val)}}
    """
    changes = []
    for vid, new_row in new.items():
        old_row = old.get(vid)
        if old_row is None:
            continue  # new venue — no prior data to compare
        diffs = {}
        for field in TRACKED:
            ov = (old_row.get(field) or "").strip()
            nv = (new_row.get(field) or "").strip()
            if ov != nv:
                diffs[field] = (ov, nv)
        if diffs:
            changes.append(
                {
                    "venue_id":   vid,
                    "venue_name": (new_row.get("venue") or vid),
                    "fields":     diffs,
                }
            )
    return changes


def patch_tips(
    tips: list[dict],
    changes: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Apply venue changes to tips in-place.
    Returns (updated_tips, list_of_patch_records) where each record has
    {id, venue, venue_id, fields: {field: (old, new)}}.
    """
    # Build lookup: venue_id → new field values
    patches: dict[str, dict] = {}
    for ch in changes:
        vid = ch["venue_id"]
        patches[vid] = {field: nv for field, (_, nv) in ch["fields"].items()}

    patch_records: list[dict] = []
    for tip in tips:
        vid = tip.get("venue_id", "")
        if vid not in patches:
            continue
        p = patches[vid]
        changed_fields = {}
        for field, new_val in p.items():
            # tips.json stores lat/lng as floats; convert if needed
            if field in ("lat", "lng"):
                try:
                    typed: object = round(float(new_val), 5) if new_val else None
                except ValueError:
                    typed = None
            else:
                typed = new_val
            if tip.get(field) != typed:
                changed_fields[field] = (tip.get(field), typed)
                tip[field] = typed
        if changed_fields:
            patch_records.append({
                "id":       tip.get("id", ""),
                "venue":    tip.get("venue", ""),
                "venue_id": vid,
                "fields":   changed_fields,
            })
    return tips, patch_records


def _write_diffs(changes: list[dict], out_path: str) -> None:
    """
    Write venue diffs to a JSON file consumable by sync_to_d1.py --venue-changes.
    Format: [{venue_id, field, old_value, new_value, detected_at}, ...]
    detected_at = current unix timestamp (best available approximation).
    """
    import time as _time
    ts = int(_time.time())
    records = []
    for ch in changes:
        for field, (old_v, new_v) in ch["fields"].items():
            records.append({
                "venue_id":    ch["venue_id"],
                "field":       field,
                "old_value":   old_v or None,
                "new_value":   new_v or None,
                "detected_at": ts,
            })
    Path(out_path).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Wrote %d diff record(s) to %s", len(records), out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff two checkins.csv snapshots and sync venue changes into tips.json"
    )
    parser.add_argument("--old",     required=True, help="Path to the archived (old) checkins.csv")
    parser.add_argument("--new",     required=True, help="Path to the freshly-fetched checkins.csv")
    parser.add_argument("--tips",    required=True, help="Path to tips.json")
    parser.add_argument("--out",     default=None,
                        help="Write venue diffs as JSON to this path (for sync_to_d1.py --venue-changes)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    old_path  = Path(args.old)
    new_path  = Path(args.new)
    tips_path = Path(args.tips)

    for p in (old_path, new_path):
        if not p.exists():
            log.error("File not found: %s", p)
            sys.exit(1)

    log.info("Loading old snapshot: %s", old_path)
    old = load_csv_by_venue(old_path)
    log.info("Loading new snapshot: %s", new_path)
    new = load_csv_by_venue(new_path)

    log.info("Old snapshot: %d unique venue_ids", len(old))
    log.info("New snapshot: %d unique venue_ids", len(new))

    changes = detect_changes(old, new)

    if not changes:
        log.info("No venue changes detected.")
    else:
        log.info("%d venue(s) changed:", len(changes))
        for ch in changes:
            field_summary = ", ".join(
                f"{f}: {ov!r}→{nv!r}" for f, (ov, nv) in ch["fields"].items()
            )
            log.info("  %-26s  %s  [%s]", ch["venue_id"], ch["venue_name"], field_summary)

    # ── Patch tips.json ────────────────────────────────────────────────────────
    if not tips_path.exists():
        log.info("tips.json not found at %s — skipping tips patch.", tips_path)
        if args.dry_run:
            log.info("Dry run complete.")
        return

    tips = json.loads(tips_path.read_text(encoding="utf-8"))
    log.info("Loaded %d tips from %s", len(tips), tips_path)

    tips, patch_records = patch_tips(tips, changes)

    if not patch_records:
        log.info("No tips needed updating.")
    else:
        log.info("")
        log.info("── Updated tips (%d) ─────────────────────────────────", len(patch_records))
        for i, rec in enumerate(patch_records, 1):
            field_summary = ", ".join(
                f"{f}: {ov!r} → {nv!r}" for f, (ov, nv) in rec["fields"].items()
            )
            log.info("  %2d. [%s]  %s", i, rec["id"], rec["venue"])
            log.info("       venue_id: %s", rec["venue_id"])
            log.info("       %s", field_summary)
        log.info("─────────────────────────────────────────────────────")
        log.info("")

    if args.dry_run:
        log.info("Dry run — tips.json not written.")
        if args.out and changes:
            _write_diffs(changes, args.out)
        return

    if patch_records:
        tips_path.write_text(
            json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Saved %s", tips_path)
    else:
        log.info("tips.json unchanged.")

    if args.out and changes:
        _write_diffs(changes, args.out)


if __name__ == "__main__":
    main()
