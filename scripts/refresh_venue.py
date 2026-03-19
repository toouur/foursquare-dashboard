"""
refresh_venue.py — Re-fetch venue info from Foursquare and patch all matching
rows in checkins.csv with the latest name, category, location, etc.

Uses /v2/users/self/checkins (free) to get venue info by fetching check-ins
near a known timestamp, extracting the embedded venue object.

Use cases:
  - Venue renamed (same venue_id, new name)
  - Venue moved (new lat/lng/address)
  - Venue merged into another (old venue_id → new venue_id; pass --new-venue-id)

Usage:
    python scripts/refresh_venue.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --csv data/checkins.csv \\
        --venue-id 4d8f90e3cb9b224b49d99d41

    # Merged venue: remap venue_id in CSV to new ID, pull info from new venue
    python scripts/refresh_venue.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --csv data/checkins.csv \\
        --venue-id 4d8f90e3cb9b224b49d99d41 \\
        --new-venue-id 5c44a5c94a7aae002cd3efa3

    # Preview changes without writing
    python scripts/refresh_venue.py ... --dry-run

Fields updated per matching row in checkins.csv:
    venue, venue_id (if --new-venue-id), venue_url,
    city, state, country, neighborhood, lat, lng, address, category

Fields updated per matching tip in tips.json (auto-detected next to CSV):
    venue, venue_id (if --new-venue-id), city, country, lat, lng, category

Fields never touched:
    date, shout, source_app, source_url, with_name, with_id (CSV)
    id, ts, text, agree_count, disagree_count, closed (tips)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

CHECKINS_API = "https://api.foursquare.com/v2/users/self/checkins"
API_V = "20231201"

FIELDS = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
]


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def fetch_venue_via_checkin(token: str, venue_id: str, timestamps: list[int]) -> dict | None:
    """
    Extract current venue info by fetching check-ins around known timestamps.
    Uses /v2/users/self/checkins (free) instead of /v2/venues/{id} (paid).
    Tries up to 3 timestamps (most recent first) before giving up.
    """
    for ts in sorted(timestamps, reverse=True)[:3]:
        try:
            resp = requests.get(
                CHECKINS_API,
                params={
                    "oauth_token": token, "v": API_V,
                    "limit": 50,
                    "beforeTimestamp": ts + 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("meta", {}).get("code") != 200:
                continue
            items = data.get("response", {}).get("checkins", {}).get("items", [])
            for ci in items:
                if str(ci.get("venue", {}).get("id", "")) == venue_id:
                    log.info("Found venue info via check-in at ts=%d", ts)
                    return ci.get("venue")
        except Exception as exc:
            log.warning("Error fetching check-ins around ts=%d: %s", ts, exc)
    log.error(
        "Could not find a check-in for venue_id=%s in %d timestamp(s) tried.",
        venue_id, min(3, len(timestamps)),
    )
    return None


def venue_to_patch(venue: dict, override_id: str | None = None) -> dict:
    """Convert a Foursquare venue object to a dict of CSV fields to patch."""
    loc = venue.get("location") or {}
    cats = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    vid = override_id or str(venue.get("id") or "")
    lat = loc.get("lat")
    lng = loc.get("lng")
    return {
        "venue":        (venue.get("name") or "").strip(),
        "venue_id":     vid,
        "venue_url":    f"https://foursquare.com/v/{vid}" if vid else "",
        "city":         (loc.get("city") or "").strip(),
        "state":        (loc.get("state") or "").strip(),
        "country":      (loc.get("country") or "").strip(),
        "neighborhood": (loc.get("neighborhood") or "").strip(),
        "lat":          str(lat) if lat is not None else "",
        "lng":          str(lng) if lng is not None else "",
        "address":      (loc.get("address") or "").strip(),
        "category":     (primary.get("name") or "").strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch venue info in checkins.csv")
    parser.add_argument("--token",        default="",                  help="Foursquare OAuth token")
    parser.add_argument("--csv",          default="data/checkins.csv", help="Path to checkins.csv")
    parser.add_argument("--venue-id",     required=True,               help="Venue ID to find in CSV")
    parser.add_argument("--new-venue-id", default="",                  help="If merged: fetch info from this ID and update venue_id in CSV")
    parser.add_argument("--tips",         default="",                  help="Path to tips.json (default: auto-detect next to CSV)")
    parser.add_argument("--dry-run",      action="store_true",         help="Show what would change without writing")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        return

    csv_path = Path(args.csv)
    if not csv_path.exists():
        log.error("CSV not found: %s", csv_path)
        return

    old_venue_id = args.venue_id.strip()
    new_venue_id = args.new_venue_id.strip()
    fetch_id = new_venue_id or old_venue_id

    # ── Load CSV ─────────────────────────────────────────────────────────────
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    matching = [r for r in rows if r.get("venue_id", "").strip() == old_venue_id]
    if not matching:
        log.info("No rows found with venue_id=%s — nothing to do.", old_venue_id)
        return
    log.info("Found %d row(s) with venue_id=%s", len(matching), old_venue_id)

    # ── Resolve timestamps for API lookup ─────────────────────────────────────
    if new_venue_id:
        # Merged venue: look up check-ins at the new venue_id
        new_matches = [r for r in rows if r.get("venue_id", "").strip() == new_venue_id]
        timestamps = [int(r["date"]) for r in new_matches if r.get("date", "").isdigit()]
        if not timestamps:
            log.error(
                "No check-ins found for new venue_id=%s in CSV. "
                "Cannot fetch venue info — try a full re-fetch first, or patch manually.",
                new_venue_id,
            )
            return
    else:
        timestamps = [int(r["date"]) for r in matching if r.get("date", "").isdigit()]

    # ── Fetch fresh venue info via check-ins endpoint (free) ─────────────────
    log.info("Fetching venue info for %s via check-ins endpoint …", fetch_id)
    venue = fetch_venue_via_checkin(token, fetch_id, timestamps)
    if not venue:
        return

    patch = venue_to_patch(venue, override_id=new_venue_id if new_venue_id else None)
    log.info("Venue from API: %s | %s, %s", patch["venue"], patch["city"], patch["country"])
    log.info("Patch: %s", json.dumps(patch, ensure_ascii=False))

    # ── Show diff and apply ───────────────────────────────────────────────────
    changed = 0
    for row in rows:
        if row.get("venue_id", "").strip() != old_venue_id:
            continue
        diffs = {k: (row.get(k, ""), v) for k, v in patch.items() if row.get(k, "") != v}
        if diffs:
            changed += 1
            for field, (old_val, new_val) in diffs.items():
                log.info("  row ts=%s  %s: %r → %r", row.get("date"), field, old_val, new_val)
        if not args.dry_run:
            row.update(patch)

    log.info("%d of %d row(s) have changes.", changed, len(matching))

    if args.dry_run:
        log.info("Dry run — CSV not written.")
        # Still show tips diff in dry-run
        tips_path = Path(args.tips) if args.tips else csv_path.parent / "tips.json"
        if tips_path.exists():
            tips = json.loads(tips_path.read_text(encoding="utf-8"))
            tip_patch = {
                "venue":    patch["venue"],
                "venue_id": patch["venue_id"],
                "city":     patch["city"],
                "country":  patch["country"],
                "lat":      round(float(patch["lat"]), 5) if patch["lat"] else None,
                "lng":      round(float(patch["lng"]), 5) if patch["lng"] else None,
                "category": patch["category"],
            }
            for t in tips:
                if t.get("venue_id", "") != old_venue_id:
                    continue
                diffs = {k: (t.get(k), v) for k, v in tip_patch.items() if t.get(k) != v}
                for field, (old_val, new_val) in diffs.items():
                    log.info("  tip %s  %s: %r → %r", t.get("id", ""), field, old_val, new_val)
        return

    if changed == 0:
        log.info("Nothing changed — CSV not written.")
        return

    # ── Write updated CSV ─────────────────────────────────────────────────────
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    log.info("Saved %s (%d total rows, %d updated).", csv_path, len(rows), changed)

    # ── Patch tips.json (auto-detected next to checkins.csv) ─────────────────
    tips_path = Path(args.tips) if args.tips else csv_path.parent / "tips.json"
    if not tips_path.exists():
        return
    tips = json.loads(tips_path.read_text(encoding="utf-8"))
    tip_patch = {
        "venue":    patch["venue"],
        "venue_id": patch["venue_id"],
        "city":     patch["city"],
        "country":  patch["country"],
        "lat":      round(float(patch["lat"]), 5) if patch["lat"] else None,
        "lng":      round(float(patch["lng"]), 5) if patch["lng"] else None,
        "category": patch["category"],
    }
    tips_changed = 0
    for t in tips:
        if t.get("venue_id", "") != old_venue_id:
            continue
        diffs = {k: (t.get(k), v) for k, v in tip_patch.items() if t.get(k) != v}
        if diffs:
            tips_changed += 1
            for field, (old_val, new_val) in diffs.items():
                log.info("  tip %s  %s: %r → %r", t.get("id", ""), field, old_val, new_val)
            t.update(tip_patch)
    if tips_changed:
        tips_path.write_text(json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Saved %s (%d tip(s) updated).", tips_path, tips_changed)
    else:
        log.info("No matching tips found in %s.", tips_path)


if __name__ == "__main__":
    main()
