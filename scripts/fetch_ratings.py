# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_ratings.py — Sync venue likes from Foursquare API into venueRatings.json.

Likes:    GET /v2/users/self/venuelikes  (returns response.venues.{count,items})
Okays:    /v2/users/self/venueokays   — 402 on most tokens; preserved from existing file.
Dislikes: /v2/users/self/venuedislikes — 402 on most tokens; preserved from existing file.

Merges fresh likes data into existing venueRatings.json, preserving:
  - All existing okays and dislikes (unchanged).
  - createdAt values for venues already in the file.
  - Any extra fields stored on existing entries.

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

USERS_API = "https://api.foursquare.com/v2/users/self/{list_id}"
API_V     = "20231201"
LIMIT     = 200
SLEEP     = 0.35


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def fetch_venue_list(token: str, list_id: str) -> list[dict] | None:
    """
    Fetch all venues from a /v2/users/self/{list_id} endpoint.

    Returns a list of dicts with id, name, url fields, or None if the
    endpoint is unavailable (e.g. 402 Forbidden).

    Note: the `count` field in the response equals items returned on this
    page, not the total, so pagination is driven by checking whether the
    page was full (len == LIMIT).
    """
    url = USERS_API.format(list_id=list_id)

    # Check availability first with a small probe
    try:
        probe = requests.get(
            url,
            params={"oauth_token": token, "v": API_V, "limit": 1, "offset": 0},
            timeout=30,
        )
    except requests.RequestException as exc:
        log.error("Network error probing %s: %s", list_id, exc)
        return None

    if probe.status_code == 402:
        log.warning("%s: 402 Forbidden — endpoint not accessible with this token; preserving existing data.", list_id)
        return None
    probe.raise_for_status()
    probe_data = probe.json()
    if probe_data.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error for {list_id}: {probe_data.get('meta')}")

    items: list[dict] = []
    offset = 0

    while True:
        resp = requests.get(
            url,
            params={"oauth_token": token, "v": API_V, "limit": LIMIT, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            raise RuntimeError(f"API error for {list_id}: {data.get('meta')}")

        raw_venues = data.get("response", {}).get("venues", {}).get("items", [])
        if not raw_venues:
            break

        for venue in raw_venues:
            vid = str(venue.get("id") or "").strip()
            if not vid:
                continue
            items.append({
                "id":   vid,
                "name": (venue.get("name") or "").strip(),
                "url":  (venue.get("canonicalUrl") or "").strip(),
            })

        log.info("%s: fetched %d items so far (page size: %d)", list_id, len(items), len(raw_venues))
        # Stop when page is not full — means we're at the end
        if len(raw_venues) < LIMIT:
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

    # Auto-resolve output path
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

    # Build lookups for existing entries (to preserve createdAt + extra fields)
    existing_likes_by_id   = {e["id"]: e for e in existing.get("venueLikes",    []) if e.get("id")}
    existing_okays_by_id   = {e["id"]: e for e in existing.get("venueOkays",    []) if e.get("id")}
    existing_dislikes_by_id = {e["id"]: e for e in existing.get("venueDislikes", []) if e.get("id")}

    changed = False
    result: dict = {}

    # ── Likes: fetch from /v2/users/self/venuelikes ──────────────────────────
    log.info("Fetching venuelikes …")
    try:
        fresh_likes = fetch_venue_list(token, "venuelikes")
    except Exception as exc:
        log.error("Failed to fetch venuelikes: %s", exc)
        fresh_likes = None

    if fresh_likes is not None:
        # Merge: API items first (in exact API order = newest-liked first),
        # then append historical items that no longer appear in the API response
        # (un-liked or removed venues we want to preserve).
        fresh_ids = {item["id"] for item in fresh_likes}
        merged_likes = []
        for item in fresh_likes:
            old = existing_likes_by_id.get(item["id"], {})
            merged_item = {**old, **item}  # fresh fields override old
            if "createdAt" not in merged_item:
                merged_item["createdAt"] = 0
            merged_likes.append(merged_item)
        # Preserve historical entries absent from the current API response
        for entry in existing.get("venueLikes", []):
            if entry.get("id") and entry["id"] not in fresh_ids:
                merged_likes.append(entry)

        old_count = len(existing.get("venueLikes", []))
        log.info("venueLikes: %d → %d items (delta: %+d)",
                 old_count, len(merged_likes), len(merged_likes) - old_count)

        result["venueLikes"] = merged_likes
        if merged_likes != existing.get("venueLikes", []):
            changed = True
    else:
        log.info("venueLikes: keeping existing %d items", len(existing.get("venueLikes", [])))
        result["venueLikes"] = existing.get("venueLikes", [])

    # ── Okays / Dislikes: endpoints always return 402; preserve existing ────────
    for json_key in ("venueOkays", "venueDislikes"):
        existing_items = existing.get(json_key, [])
        log.info("%s: preserving existing %d items (endpoint not accessible)", json_key, len(existing_items))
        result[json_key] = existing_items

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
