# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_venue_rating.py — Retrieve the current Foursquare rating for specific
venue IDs and sync their metadata into venueRatings.json.

For each venue ID:
  1. Fetches venue details (name, canonicalUrl) from /v2/venues/{id}.
  2. Determines rating by checking:
       - venue.like == true  → venueLikes
       - else: checks venuedislikes list for presence → venueDislikes
       - else → venueOkays (neutral)
  3. Updates the matching entry in venueRatings.json (or adds it if missing).

Use this when a venue is already rated on Foursquare but missing from
the local venueRatings.json (e.g. after importing from a fresh export).

Usage:
    python scripts/fetch_venue_rating.py --token TOKEN --venue-id ID1,ID2
    python scripts/fetch_venue_rating.py --token TOKEN --venue-id ID1 --out data/venueRatings.json

Outputs:
  - Prints CHANGED=true/false to stdout (for GitHub Actions >> $GITHUB_OUTPUT).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

BASE   = "https://api.foursquare.com/v2"
API_V  = "20231201"
SLEEP  = 0.35
LIMIT  = 200


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def _get(url: str, params: dict, timeout: int = 30) -> dict:
    params.setdefault("v", API_V)
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if d.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error {url}: {d.get('meta')}")
    return d.get("response", {})


def fetch_venue_details(token: str, venue_id: str) -> dict:
    """Fetch name, url, and like status for a venue."""
    resp = _get(
        f"{BASE}/venues/{venue_id}",
        {"oauth_token": token},
    )
    v = resp.get("venue") or {}
    return {
        "id":        venue_id,
        "name":      (v.get("name") or "").strip(),
        "url":       (v.get("canonicalUrl") or "").strip(),
        "liked":     bool(v.get("like")),
    }


def fetch_dislike_ids(token: str) -> set[str]:
    """Fetch all venue IDs from the venuedislikes list (usually small)."""
    ids: set[str] = []
    offset = 0
    # Probe total
    probe = _get(
        f"{BASE}/lists/venuedislikes",
        {"oauth_token": token, "limit": 1, "offset": 0},
    )
    total = probe.get("list", {}).get("listItems", {}).get("count", 0)
    log.info("venuedislikes: %d total items", total)

    while True:
        resp = _get(
            f"{BASE}/lists/venuedislikes",
            {"oauth_token": token, "limit": LIMIT, "offset": offset},
        )
        raw = resp.get("list", {}).get("listItems", {}).get("items", [])
        if not raw:
            break
        for item in raw:
            vid = str((item.get("venue") or {}).get("id") or "").strip()
            if vid:
                ids.append(vid)
        if len(ids) >= total:
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return set(ids)


def find_created_at(token: str, list_id: str, venue_id: str) -> int:
    """Search a list for a venue and return its createdAt timestamp, or 0."""
    offset = 0
    probe = _get(
        f"{BASE}/lists/{list_id}",
        {"oauth_token": token, "limit": 1, "offset": 0},
    )
    total = probe.get("list", {}).get("listItems", {}).get("count", 0)

    while True:
        resp = _get(
            f"{BASE}/lists/{list_id}",
            {"oauth_token": token, "limit": LIMIT, "offset": offset},
        )
        raw = resp.get("list", {}).get("listItems", {}).get("items", [])
        if not raw:
            break
        for item in raw:
            vid = str((item.get("venue") or {}).get("id") or "").strip()
            if vid == venue_id:
                return int(item.get("createdAt") or 0)
        offset += LIMIT
        if offset >= total:
            break
        time.sleep(SLEEP)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Foursquare rating for specific venue IDs and sync to venueRatings.json"
    )
    parser.add_argument("--token",    default="", help="Foursquare OAuth token")
    parser.add_argument("--venue-id", required=True, help="Venue ID(s), comma-separated")
    parser.add_argument("--out",      default="", help="Output venueRatings.json path (auto-resolved if omitted)")
    parser.add_argument("--no-created-at", action="store_true",
                        help="Skip fetching createdAt (faster, but loses rating timestamp)")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    venue_ids = [v.strip() for v in args.venue_id.split(",") if v.strip()]
    if not venue_ids:
        log.error("No venue IDs provided.")
        print("CHANGED=false")
        return

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    else:
        candidates = [
            Path("data/venueRatings.json"),
            Path("C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json"),
        ]
        out_path = next((p for p in candidates if p.exists()), candidates[0])

    # Load existing
    existing: dict = {"venueLikes": [], "venueOkays": [], "venueDislikes": []}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not read existing %s: %s", out_path, exc)

    # Build lookup of existing entries
    existing_by_id: dict[str, tuple[str, dict]] = {}  # venue_id → (json_key, entry)
    for json_key in ("venueLikes", "venueOkays", "venueDislikes"):
        for entry in existing.get(json_key, []):
            vid = entry.get("id", "")
            if vid:
                existing_by_id[vid] = (json_key, entry)

    # Fetch dislike IDs once (usually a small list)
    log.info("Fetching dislike list to detect dislikes…")
    try:
        dislike_ids = fetch_dislike_ids(token)
    except Exception as exc:
        log.warning("Could not fetch dislikes list: %s — will not detect dislikes.", exc)
        dislike_ids = set()
    time.sleep(SLEEP)

    # Process each venue
    result: dict = {
        "venueLikes":    list(existing.get("venueLikes", [])),
        "venueOkays":    list(existing.get("venueOkays", [])),
        "venueDislikes": list(existing.get("venueDislikes", [])),
    }
    changed = False

    for venue_id in venue_ids:
        log.info("Processing venue %s …", venue_id)
        try:
            details = fetch_venue_details(token, venue_id)
        except Exception as exc:
            log.error("Failed to fetch venue %s: %s", venue_id, exc)
            continue
        time.sleep(SLEEP)

        # Determine which list it belongs to
        if details["liked"]:
            json_key = "venueLikes"
            list_id  = "venuelikes"
        elif venue_id in dislike_ids:
            json_key = "venueDislikes"
            list_id  = "venuedislikes"
        else:
            json_key = "venueOkays"
            list_id  = "venueokays"

        log.info("Venue %s (%s) → %s", venue_id, details["name"], json_key)

        # Fetch createdAt from the list (can skip with --no-created-at)
        created_at = 0
        if not args.no_created_at:
            log.info("  Fetching createdAt from %s…", list_id)
            try:
                created_at = find_created_at(token, list_id, venue_id)
                log.info("  createdAt = %s", created_at)
            except Exception as exc:
                log.warning("  Could not fetch createdAt: %s", exc)
            time.sleep(SLEEP)

        new_entry = {
            "id":        venue_id,
            "name":      details["name"],
            "url":       details["url"],
            "createdAt": created_at,
        }

        # Remove venue from all lists first (it may have moved categories)
        for key in ("venueLikes", "venueOkays", "venueDislikes"):
            before = len(result[key])
            result[key] = [e for e in result[key] if e.get("id") != venue_id]
            if len(result[key]) != before:
                log.info("  Removed from %s", key)

        # Add to the correct list
        old_entry, old_key = None, None
        if venue_id in existing_by_id:
            old_key, old_entry = existing_by_id[venue_id]

        if old_entry:
            # Preserve any extra fields from the existing entry
            merged = {**old_entry, **new_entry}
        else:
            merged = new_entry

        result[json_key].append(merged)
        log.info("  Added to %s", json_key)
        changed = True

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Wrote updated venueRatings.json → %s", out_path)
    else:
        log.info("No changes.")

    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
