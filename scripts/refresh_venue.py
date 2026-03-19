"""
refresh_venue.py — Re-fetch venue info from Foursquare and patch all matching
rows in checkins.csv with the latest name, category, location, etc.

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

Fields updated per matching row:
    venue, venue_id (if --new-venue-id), venue_url,
    city, state, country, neighborhood, lat, lng, address, category

Fields never touched:
    date, shout, source_app, source_url, with_name, with_id
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

VENUE_DETAIL_API = "https://api.foursquare.com/v2/venues/{vid}"
API_V = "20231201"

FIELDS = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
]

# Fields we will overwrite from the fresh venue API response
VENUE_FIELDS = {"venue", "venue_id", "venue_url", "city", "state", "country",
                "neighborhood", "lat", "lng", "address", "category"}


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def fetch_venue(token: str, venue_id: str) -> dict | None:
    """Fetch current venue details from /v2/venues/{id}."""
    try:
        resp = requests.get(
            VENUE_DETAIL_API.format(vid=venue_id),
            params={"oauth_token": token, "v": API_V},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            log.error("API error: %s", data.get("meta"))
            return None
        return data.get("response", {}).get("venue")
    except Exception as exc:
        log.error("Failed to fetch venue %s: %s", venue_id, exc)
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
    parser.add_argument("--token",       default="",                   help="Foursquare OAuth token")
    parser.add_argument("--csv",         default="data/checkins.csv",  help="Path to checkins.csv")
    parser.add_argument("--venue-id",    required=True,                help="Venue ID to find in CSV")
    parser.add_argument("--new-venue-id", default="",                  help="If the venue was merged, fetch info from this ID instead (and update venue_id in CSV)")
    parser.add_argument("--dry-run",     action="store_true",          help="Show what would change without writing")
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
    fetch_id = new_venue_id or old_venue_id  # fetch info from new ID if provided

    # ── Load CSV ─────────────────────────────────────────────────────────────
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    matching = [r for r in rows if r.get("venue_id", "").strip() == old_venue_id]
    if not matching:
        log.info("No rows found with venue_id=%s — nothing to do.", old_venue_id)
        return
    log.info("Found %d row(s) with venue_id=%s", len(matching), old_venue_id)

    # ── Fetch fresh venue info ────────────────────────────────────────────────
    log.info("Fetching venue info for %s …", fetch_id)
    venue = fetch_venue(token, fetch_id)
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


if __name__ == "__main__":
    main()
