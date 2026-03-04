"""
fetch_checkins.py  –  Fetch new check-ins from Foursquare/Swarm API and merge
                       into checkins.csv. Designed to run in GitHub Actions.

Usage:
    python fetch_checkins.py --token $SWARM_TOKEN [--csv checkins.csv]

The script will:
  1. Load existing checkins.csv (gets most recent timestamp).
  2. Fetch all check-ins newer than that timestamp via the Foursquare v2 API.
  3. Append new rows to checkins.csv (deduplicated by timestamp).
  4. Exit with code 0 always; prints "CHANGED=true" to stdout if rows were added
     (so GitHub Actions can conditionally rebuild).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

API_BASE = "https://api.foursquare.com/v2/users/self/checkins"
API_V    = "20231201"


# ── CSV schema (must match the existing checkins.csv) ─────────────────────────
FIELDS = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
]


def load_existing(csv_path: Path) -> tuple[list[dict], set[str]]:
    """Return (rows, existing_timestamps_set)."""
    if not csv_path.exists():
        return [], set()
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    timestamps = {r["date"] for r in rows if r.get("date", "").strip()}
    return rows, timestamps


def fetch_page(token: str, offset: int, after_ts: int, limit: int = 250) -> dict:
    params = {
        "oauth_token": token,
        "v":           API_V,
        "limit":       limit,
        "offset":      offset,
    }
    if after_ts > 0:
        params["afterTimestamp"] = str(after_ts)
    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_to_row(ci: dict) -> dict:
    """Convert a Foursquare checkin API object to a CSV row dict."""
    venue   = ci.get("venue", {})
    loc     = venue.get("location", {})
    cats    = venue.get("categories", [])
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})

    with_items = ci.get("with", [])
    with_names = ", ".join(w.get("firstName", "") + " " + w.get("lastName", "")
                           for w in with_items).strip()
    with_ids   = ", ".join(str(w.get("id", "")) for w in with_items)

    source = ci.get("source", {})

    return {
        "date":       str(ci.get("createdAt", "")),
        "venue":      venue.get("name", ""),
        "venue_id":   venue.get("id", ""),
        "venue_url":  f"https://foursquare.com/v/{venue.get('id', '')}",
        "city":       loc.get("city", ""),
        "state":      loc.get("state", ""),
        "country":    loc.get("country", ""),
        "neighborhood": loc.get("neighborhood", ""),
        "lat":        str(loc.get("lat", "")),
        "lng":        str(loc.get("lng", "")),
        "address":    loc.get("address", ""),
        "category":   primary.get("name", ""),
        "shout":      ci.get("shout", ""),
        "source_app": source.get("name", ""),
        "source_url": source.get("url", ""),
        "with_name":  with_names,
        "with_id":    with_ids,
    }


def fetch_all_new(token: str, after_ts: int) -> list[dict]:
    """Page through the API until we have all check-ins newer than after_ts."""
    new_rows: list[dict] = []
    offset = 0
    limit  = 250

    while True:
        log.info("Fetching offset=%d after_ts=%d …", offset, after_ts)
        try:
            data = fetch_page(token, offset, after_ts, limit)
        except requests.HTTPError as exc:
            log.error("API error: %s", exc)
            break

        items = data.get("response", {}).get("checkins", {}).get("items", [])
        if not items:
            break

        for ci in items:
            new_rows.append(api_to_row(ci))

        # If we got fewer than a full page, we're done.
        if len(items) < limit:
            break

        offset += limit
        time.sleep(0.3)   # be polite

    return new_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True,   help="Foursquare OAuth token")
    parser.add_argument("--csv",   default="checkins.csv", help="Path to checkins CSV")
    args = parser.parse_args()

    csv_path = Path(args.csv)

    # Load existing data
    existing_rows, existing_ts = load_existing(csv_path)
    log.info("Existing rows: %d", len(existing_rows))

    # Most recent timestamp → only fetch newer
    after_ts = 0
    if existing_ts:
        after_ts = max(int(t) for t in existing_ts if t.strip().isdigit())
    log.info("Most recent existing timestamp: %d", after_ts)

    # Fetch from API
    new_rows = fetch_all_new(args.token, after_ts)
    log.info("API returned %d rows", len(new_rows))

    # Deduplicate
    added = [r for r in new_rows if r["date"] not in existing_ts]
    log.info("New (de-duped): %d", len(added))

    if not added:
        print("CHANGED=false")
        return

    # Sort all rows oldest-first, append new ones
    all_rows = existing_rows + added
    all_rows.sort(key=lambda r: int(r.get("date", 0) or 0))

    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    log.info("Wrote %d total rows → %s", len(all_rows), csv_path)
    print("CHANGED=true")


if __name__ == "__main__":
    main()
