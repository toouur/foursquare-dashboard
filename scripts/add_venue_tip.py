"""
add_venue_tip.py — Fetch the user's tip(s) from a single Foursquare venue
(by URL or venue ID) and append any new ones to tips.json, marked closed=True.

Use case: tips on closed/deleted venues that /users/self/tips silently omits
and the automated sweep missed. Paste the Foursquare venue URL from a browser
and the script handles the rest.

Usage:
    python scripts/add_venue_tip.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --venue "https://foursquare.com/v/some-name/4d8f90e3cb9b224b49d99d41" \\
        --tips  private-data/tips.json [--dry-run]

Exit codes:
    0 — success (tip added, already present, or none found)
    1 — error (bad token, network failure)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

VENUE_API = "https://api.foursquare.com/v2/venues/{vid}/tips"
API_V     = "20231201"
SLEEP     = 0.35


def resolve_token(cli: str | None) -> str:
    t = (cli or "").strip()
    return t or os.environ.get("FOURSQUARE_TOKEN", "").strip()


def extract_venue_id(url_or_id: str) -> str:
    """Extract a 24-char hex venue ID from a URL or return the raw string."""
    s = url_or_id.strip().rstrip("/")
    m = re.search(r"([0-9a-f]{24})", s)
    if m:
        return m.group(1)
    # Fallback: last path segment (handles numeric or other formats)
    return s.split("/")[-1]


def api_tip_to_dict(t: dict) -> dict:
    """Convert a Foursquare tip API object to our tips.json schema."""
    venue   = t.get("venue") or {}
    loc     = venue.get("location") or {}
    cats    = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    lat     = loc.get("lat")
    lng     = loc.get("lng")
    return {
        "id":             str(t.get("id") or ""),
        "ts":             int(t.get("createdAt") or 0),
        "text":           (t.get("text") or "").strip(),
        "venue":          (venue.get("name") or "").strip(),
        "venue_id":       str(venue.get("id") or ""),
        "city":           (loc.get("city") or "").strip(),
        "country":        (loc.get("country") or "").strip(),
        "lat":            round(float(lat), 5) if lat is not None else None,
        "lng":            round(float(lng), 5) if lng is not None else None,
        "category":       (primary.get("name") or "").strip(),
        "agree_count":    int(t.get("agreeCount") or 0),
        "disagree_count": int(t.get("disagreeCount") or 0),
        "closed":         True,
    }


def fetch_venue_tips(token: str, venue_id: str) -> list[dict]:
    resp = requests.get(
        VENUE_API.format(vid=venue_id),
        params={"oauth_token": token, "v": API_V, "limit": 200, "filter": "self"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error: {data.get('meta')}")
    return data.get("response", {}).get("tips", {}).get("items", [])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add user tip(s) from a single Foursquare venue to tips.json"
    )
    parser.add_argument("--token",   default="",      help="Foursquare OAuth token")
    parser.add_argument("--venue",   required=True,   help="Foursquare venue URL or venue ID")
    parser.add_argument("--tips",    required=True,   help="Path to tips.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        sys.exit(1)

    venue_id = extract_venue_id(args.venue)
    log.info("Venue ID: %s", venue_id)

    tips_path = Path(args.tips)
    tips: list[dict] = []
    if tips_path.exists():
        tips = json.loads(tips_path.read_text(encoding="utf-8"))
    existing_ids = {t.get("id") for t in tips}
    existing_venue_ids = {t.get("venue_id") for t in tips}

    log.info("Loaded %d existing tips; checking venue %s …", len(tips), venue_id)

    try:
        raw = fetch_venue_tips(token, venue_id)
    except requests.HTTPError as exc:
        log.error("HTTP error fetching tips: %s", exc)
        sys.exit(1)
    except Exception as exc:
        log.error("Failed to fetch tips: %s", exc)
        sys.exit(1)

    if not raw:
        log.info("No tips found for venue %s.", venue_id)
        return

    log.info("API returned %d tip(s) for this venue.", len(raw))

    added: list[dict] = []
    for item in raw:
        rec = api_tip_to_dict(item)
        if rec["id"] in existing_ids:
            log.info("Tip %s already in tips.json — skipping.", rec["id"])
            continue
        log.info("New tip: [%s] venue=%r  city=%r  country=%r",
                 rec["id"], rec["venue"], rec["city"], rec["country"])
        log.info("  text: %s", rec["text"][:120])
        added.append(rec)

    if not added:
        log.info("No new tips to add.")
        return

    if args.dry_run:
        log.info("Dry run — tips.json not written (%d tip(s) would be added).", len(added))
        return

    tips.extend(added)
    tips.sort(key=lambda t: -t.get("ts", 0))
    tips_path.write_text(json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Added %d tip(s) → %s (%d total)", len(added), tips_path, len(tips))


if __name__ == "__main__":
    main()
