# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_ratings.py — Fetch venue ratings with createdAt timestamps from Foursquare API.

Queries three personal lists:
  /v2/lists/venuelikes    — liked venues
  /v2/lists/venueokays   — neutral venues
  /v2/lists/venuedislikes — disliked venues

Each endpoint supports offset pagination (limit=200).
Merges fresh data (including createdAt) into existing venueRatings.json.

Usage:
    python scripts/fetch_ratings.py --token "$FOURSQUARE_TOKEN"
    python scripts/fetch_ratings.py --token "$FOURSQUARE_TOKEN" --out data/venueRatings.json

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

LIST_API = "https://api.foursquare.com/v2/lists/{list_id}"
API_V    = "20231201"
LIMIT    = 200
SLEEP    = 0.35

LIST_KEYS = {
    "venueLikes":    "venuelikes",
    "venueOkays":    "venueokays",
    "venueDislikes": "venuedislikes",
}


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def fetch_list(token: str, list_id: str) -> list[dict]:
    """Fetch all items from a Foursquare list with pagination."""
    items: list[dict] = []
    offset = 0

    # Probe to get total count
    resp = requests.get(
        LIST_API.format(list_id=list_id),
        params={"oauth_token": token, "v": API_V, "limit": 1, "offset": 0},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error for list {list_id}: {data.get('meta')}")
    total = data.get("response", {}).get("list", {}).get("listItems", {}).get("count", 0)
    log.info("List %s: server reports %d items", list_id, total)

    while True:
        resp = requests.get(
            LIST_API.format(list_id=list_id),
            params={"oauth_token": token, "v": API_V, "limit": LIMIT, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            raise RuntimeError(f"API error for list {list_id}: {data.get('meta')}")

        raw_items = (
            data.get("response", {})
                .get("list", {})
                .get("listItems", {})
                .get("items", [])
        )
        if not raw_items:
            break

        for item in raw_items:
            venue = item.get("venue") or {}
            vid = str(venue.get("id") or "").strip()
            if not vid:
                continue
            items.append({
                "id":        vid,
                "name":      (venue.get("name") or "").strip(),
                "url":       (venue.get("canonicalUrl") or "").strip(),
                "createdAt": int(item.get("createdAt") or 0),
            })

        log.info("List %s: fetched %d / %d items", list_id, len(items), total)
        if len(items) >= total:
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--out",   default="", help="Output venueRatings.json path (auto-resolved if omitted)")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    # Auto-resolve output path: sibling of checkins.csv or default data/
    if args.out:
        out_path = Path(args.out)
    else:
        # Try common locations
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

    changed = False
    result: dict = {}

    for json_key, list_id in LIST_KEYS.items():
        log.info("Fetching %s …", list_id)
        try:
            fresh = fetch_list(token, list_id)
        except Exception as exc:
            log.error("Failed to fetch %s: %s", list_id, exc)
            # Keep existing data for this key
            result[json_key] = existing.get(json_key, [])
            continue

        # Build lookup from existing (to preserve fields not returned by API)
        old_by_id = {e["id"]: e for e in existing.get(json_key, []) if e.get("id")}

        # Merge: fresh data wins for all returned fields, preserve any extras
        merged = []
        for item in fresh:
            old = old_by_id.get(item["id"], {})
            merged_item = {**old, **item}  # fresh fields override old
            merged.append(merged_item)

        result[json_key] = merged
        old_count = len(existing.get(json_key, []))
        log.info("%s: %d → %d items (delta: %+d)",
                 json_key, old_count, len(merged), len(merged) - old_count)

        if merged != existing.get(json_key, []):
            changed = True

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Wrote updated venueRatings.json → %s", out_path)
    else:
        log.info("No changes detected.")

    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
