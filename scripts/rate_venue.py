# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
rate_venue.py — Post a like/neutral/dislike rating to Foursquare for a venue,
then update venueRatings.json locally.

Resolves the venue from either a venue ID directly or a check-in ID looked up
in checkins.csv.

Usage:
    python scripts/rate_venue.py --token TOKEN --venue-id VENUE_ID --rating like
    python scripts/rate_venue.py --token TOKEN --checkin-id CHECKIN_ID --rating neutral
    python scripts/rate_venue.py --token TOKEN --checkin-id ID1,ID2,ID3 --rating dislike

    # Multiple venue IDs at once:
    python scripts/rate_venue.py --token TOKEN --venue-id ID1,ID2 --rating like

Rating values: like | neutral | dislike

Outputs:
  - Prints CHANGED=true/false to stdout (for GitHub Actions >> $GITHUB_OUTPUT).
"""
from __future__ import annotations

import argparse
import csv
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

# Foursquare system list IDs for rating categories
RATING_LISTS = {
    "like":    "venuelikes",
    "neutral": "venueokays",
    "dislike": "venuedislikes",
}
# JSON key in venueRatings.json
RATING_KEYS = {
    "like":    "venueLikes",
    "neutral": "venueOkays",
    "dislike": "venueDislikes",
}


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def venue_ids_from_checkins(checkin_ids: list[str], csv_path: Path) -> dict[str, str]:
    """Return {checkin_id: venue_id} for the given checkin_ids."""
    result: dict[str, str] = {}
    if not csv_path.exists():
        log.error("checkins.csv not found at %s", csv_path)
        return result
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("checkin_id") or "").strip()
            vid = (row.get("venue_id") or "").strip()
            if cid in checkin_ids and vid:
                result[cid] = vid
                if len(result) == len(checkin_ids):
                    break
    return result


def _post(url: str, params: dict, timeout: int = 30) -> dict:
    params.setdefault("v", API_V)
    r = requests.post(url, params=params, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if d.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error {url}: {d.get('meta')}")
    return d.get("response", {})


def _get(url: str, params: dict, timeout: int = 30) -> dict:
    params.setdefault("v", API_V)
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if d.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error {url}: {d.get('meta')}")
    return d.get("response", {})


def add_rating(token: str, venue_id: str, rating: str) -> None:
    """Post rating to Foursquare and remove from the other two lists."""
    target_list = RATING_LISTS[rating]
    other_lists = [v for k, v in RATING_LISTS.items() if k != rating]

    # Add to target list
    log.info("Adding venue %s to %s …", venue_id, target_list)
    _post(
        f"{BASE}/lists/{target_list}/additem",
        {"oauth_token": token, "venueId": venue_id},
    )
    time.sleep(SLEEP)

    # Remove from other two lists (best-effort; ignore 400 if not present)
    for lst in other_lists:
        try:
            _post(
                f"{BASE}/lists/{lst}/deleteitem",
                {"oauth_token": token, "venueId": venue_id},
            )
            log.info("Removed venue %s from %s", venue_id, lst)
            time.sleep(SLEEP)
        except Exception as exc:
            log.debug("Could not remove %s from %s (may not be present): %s", venue_id, lst, exc)


def fetch_rating_list(token: str, list_id: str) -> list[dict]:
    """Fetch all items from a rating list with pagination."""
    items: list[dict] = []
    limit = 200
    # Probe total
    probe = _get(
        f"{BASE}/lists/{list_id}",
        {"oauth_token": token, "limit": 1, "offset": 0},
    )
    total = probe.get("list", {}).get("listItems", {}).get("count", 0)

    offset = 0
    while True:
        resp = _get(
            f"{BASE}/lists/{list_id}",
            {"oauth_token": token, "limit": limit, "offset": offset},
        )
        raw = resp.get("list", {}).get("listItems", {}).get("items", [])
        if not raw:
            break
        for item in raw:
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
        log.info("  %s: %d / %d items", list_id, len(items), total)
        if len(items) >= total:
            break
        offset += limit
        time.sleep(SLEEP)
    return items


def update_ratings_json(out_path: Path, token: str) -> bool:
    """Re-fetch all three rating lists and write venueRatings.json. Returns True if changed."""
    existing: dict = {"venueLikes": [], "venueOkays": [], "venueDislikes": []}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not read existing %s: %s", out_path, exc)

    changed = False
    result: dict = {}
    for json_key, list_id in [("venueLikes", "venuelikes"), ("venueOkays", "venueokays"), ("venueDislikes", "venuedislikes")]:
        log.info("Re-fetching %s …", list_id)
        try:
            fresh = fetch_rating_list(token, list_id)
        except Exception as exc:
            log.error("Failed to fetch %s: %s", list_id, exc)
            result[json_key] = existing.get(json_key, [])
            continue

        # Merge: fresh data wins, preserve any extras from existing
        old_by_id = {e["id"]: e for e in existing.get(json_key, []) if e.get("id")}
        merged = [{**old_by_id.get(item["id"], {}), **item} for item in fresh]
        result[json_key] = merged

        if merged != existing.get(json_key, []):
            changed = True
        log.info("%s: %d items", json_key, len(merged))

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Wrote updated venueRatings.json → %s", out_path)
    else:
        log.info("No changes in ratings.")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Rate a Foursquare venue and update venueRatings.json")
    parser.add_argument("--token",      default="", help="Foursquare OAuth token")
    parser.add_argument("--venue-id",   default="", help="Venue ID(s), comma-separated")
    parser.add_argument("--checkin-id", default="", help="Check-in ID(s) to look up venue from CSV, comma-separated")
    parser.add_argument("--rating",     required=True, choices=["like", "neutral", "dislike"],
                        help="Rating to apply: like | neutral | dislike")
    parser.add_argument("--csv",        default="", help="Path to checkins.csv (required if --checkin-id used)")
    parser.add_argument("--out",        default="", help="Output venueRatings.json path (auto-resolved if omitted)")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
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

    # Collect venue IDs
    venue_ids: list[str] = [v.strip() for v in args.venue_id.split(",") if v.strip()]

    # Resolve from check-in IDs
    if args.checkin_id:
        checkin_ids = [c.strip() for c in args.checkin_id.split(",") if c.strip()]
        if not checkin_ids:
            log.error("--checkin-id provided but empty.")
            print("CHANGED=false")
            return

        # Resolve CSV path
        if args.csv:
            csv_path = Path(args.csv)
        else:
            csv_candidates = [
                Path("data/checkins.csv"),
                Path("C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv"),
            ]
            csv_path = next((p for p in csv_candidates if p.exists()), csv_candidates[0])

        resolved = venue_ids_from_checkins(checkin_ids, csv_path)
        not_found = [c for c in checkin_ids if c not in resolved]
        if not_found:
            log.warning("Check-in IDs not found in CSV: %s", not_found)
        venue_ids.extend(resolved.values())
        log.info("Resolved check-in IDs to venue IDs: %s", resolved)

    if not venue_ids:
        log.error("No venue IDs to process. Provide --venue-id or --checkin-id.")
        print("CHANGED=false")
        return

    # Deduplicate
    seen: set[str] = set()
    unique_ids = [v for v in venue_ids if not (v in seen or seen.add(v))]  # type: ignore[func-returns-value]

    # Apply ratings
    errors = 0
    for vid in unique_ids:
        try:
            add_rating(token, vid, args.rating)
        except Exception as exc:
            log.error("Failed to rate venue %s: %s", vid, exc)
            errors += 1

    if errors == len(unique_ids):
        log.error("All rating API calls failed.")
        print("CHANGED=false")
        return

    # Re-fetch all three lists and update venueRatings.json
    changed = update_ratings_json(out_path, token)
    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
