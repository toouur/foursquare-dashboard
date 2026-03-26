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
) -> tuple[list[dict], int]:
    """
    Apply venue changes to tips in-place.
    Returns (updated_tips, count_of_patched_tips).
    """
    # Build lookup: venue_id → new field values
    patches: dict[str, dict] = {}
    for ch in changes:
        vid = ch["venue_id"]
        patches[vid] = {field: nv for field, (_, nv) in ch["fields"].items()}

    patched = 0
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
            patched += 1
            log.info(
                "  tip %-24s  venue=%r  changes: %s",
                tip.get("id", ""),
                tip.get("venue", ""),
                ", ".join(f"{k}: {ov!r}→{nv!r}" for k, (ov, nv) in changed_fields.items()),
            )
    return tips, patched


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff two checkins.csv snapshots and sync venue changes into tips.json"
    )
    parser.add_argument("--old",     required=True, help="Path to the archived (old) checkins.csv")
    parser.add_argument("--new",     required=True, help="Path to the freshly-fetched checkins.csv")
    parser.add_argument("--tips",    required=True, help="Path to tips.json")
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

    tips, patched = patch_tips(tips, changes)

    if patched == 0:
        log.info("No tips needed updating.")
    else:
        log.info("%d tip(s) updated.", patched)

    if args.dry_run:
        log.info("Dry run — tips.json not written.")
        return

    if patched > 0:
        tips_path.write_text(
            json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Saved %s", tips_path)
    else:
        log.info("tips.json unchanged.")


if __name__ == "__main__":
    main()
