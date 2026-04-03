# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
fetch_all_reactions.py — Backfill venue reactions for all unique venue IDs in checkins.csv.

For each venue ID not yet in venueRatings.json (and not previously checked),
calls GET /v2/venues/{id} to determine the current reaction:
  - venue.like == true  → venueLikes
  - else: checks venuedislikes list membership → venueDislikes
  - else → venueOkays

Tracks already-checked venue IDs in a sidecar file (--checked-out, default:
venueRatings.checked.json) so re-runs only process new venues.

Progress is saved incrementally: venueRatings.json is updated after every
batch of --batch-size venues, so interrupted runs can be safely resumed.

Usage:
    python scripts/fetch_all_reactions.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --csv   data/checkins.csv \\
        --out   data/venueRatings.json

    # Resume / incremental (skips venues already checked):
    python scripts/fetch_all_reactions.py --token TOKEN --csv ... --out ...

    # Force re-check all venues (ignores checked cache):
    python scripts/fetch_all_reactions.py --token TOKEN --csv ... --out ... --full

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
    resp = _get(f"{BASE}/venues/{venue_id}", {"oauth_token": token})
    v = resp.get("venue") or {}
    return {
        "id":    venue_id,
        "name":  (v.get("name") or "").strip(),
        "url":   (v.get("canonicalUrl") or "").strip(),
        "liked": bool(v.get("like")),
    }


def fetch_dislike_ids(token: str) -> set[str]:
    """Fetch all venue IDs from the venuedislikes list."""
    ids: list[str] = []
    offset = 0
    try:
        probe = _get(
            f"{BASE}/lists/venuedislikes",
            {"oauth_token": token, "limit": 1, "offset": 0},
        )
        total = probe.get("list", {}).get("listItems", {}).get("count", 0)
    except Exception as exc:
        log.warning("Could not probe venuedislikes: %s", exc)
        return set()

    log.info("venuedislikes: %d total items", total)
    while True:
        try:
            resp = _get(
                f"{BASE}/lists/venuedislikes",
                {"oauth_token": token, "limit": LIMIT, "offset": offset},
            )
        except Exception as exc:
            log.warning("Error fetching venuedislikes at offset %d: %s", offset, exc)
            break
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


def load_venue_ids_from_csv(csv_path: Path) -> list[str]:
    """Return unique venue IDs from checkins.csv, in order of first appearance."""
    seen: set[str] = set()
    ordered: list[str] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vid = (row.get("venue_id") or "").strip()
            if vid and vid not in seen:
                seen.add(vid)
                ordered.append(vid)
    return ordered


def load_ratings(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not read %s: %s", path, exc)
    return {"venueLikes": [], "venueOkays": [], "venueDislikes": []}


def save_ratings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checked(path: Path) -> set[str]:
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_checked(path: Path, checked: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(checked), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill venue reactions for all unique venue IDs in checkins.csv"
    )
    parser.add_argument("--token",      default="", help="Foursquare OAuth token")
    parser.add_argument("--csv",        default="", help="Path to checkins.csv")
    parser.add_argument("--out",        default="", help="Path to venueRatings.json")
    parser.add_argument("--checked-out", default="", help="Path to checked-venues sidecar file")
    parser.add_argument("--full",       action="store_true",
                        help="Re-check all venues, ignoring the checked cache")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Save progress every N venues (default: 50)")
    parser.add_argument("--limit",      type=int, default=0,
                        help="Process at most N unchecked venues (0 = all)")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    # Resolve paths
    if args.csv:
        csv_path = Path(args.csv)
    else:
        candidates = [
            Path("data/checkins.csv"),
            Path("C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv"),
        ]
        csv_path = next((p for p in candidates if p.exists()), candidates[0])

    if args.out:
        out_path = Path(args.out)
    else:
        candidates = [
            Path("data/venueRatings.json"),
            Path("C:/Users/toouur/Documents/GitHub/foursquare-data/venueRatings.json"),
        ]
        out_path = next((p for p in candidates if p.exists()), candidates[0])

    checked_path = Path(args.checked_out) if args.checked_out else out_path.parent / "venueRatings.checked.json"

    if not csv_path.exists():
        log.error("CSV not found: %s", csv_path)
        print("CHANGED=false")
        return

    # Load data
    ratings = load_ratings(out_path)
    checked = set() if args.full else load_checked(checked_path)

    # Build set of venue IDs already in ratings
    rated_ids: set[str] = set()
    for key in ("venueLikes", "venueOkays", "venueDislikes"):
        for entry in ratings.get(key, []):
            vid = entry.get("id", "")
            if vid:
                rated_ids.add(vid)

    # All unique venue IDs from CSV
    all_venue_ids = load_venue_ids_from_csv(csv_path)
    log.info("Total unique venues in CSV: %d", len(all_venue_ids))

    # Venues to process: not yet checked (and not already rated unless --full)
    if args.full:
        to_check = all_venue_ids
    else:
        to_check = [v for v in all_venue_ids if v not in checked]

    log.info("Venues to check: %d  (already checked: %d)", len(to_check), len(checked))

    if args.limit > 0:
        to_check = to_check[:args.limit]
        log.info("Processing first %d venues (--limit)", len(to_check))

    if not to_check:
        log.info("Nothing to do.")
        print("CHANGED=false")
        return

    # Fetch dislike IDs once
    log.info("Fetching dislike list …")
    try:
        dislike_ids = fetch_dislike_ids(token)
        log.info("  %d dislikes found", len(dislike_ids))
    except Exception as exc:
        log.warning("Could not fetch dislikes: %s — dislikes will be treated as neutral", exc)
        dislike_ids = set()
    time.sleep(SLEEP)

    # Process venues
    total_changed = 0
    errors = 0

    for i, venue_id in enumerate(to_check, 1):
        log.info("[%d/%d] venue %s", i, len(to_check), venue_id)

        try:
            details = fetch_venue_details(token, venue_id)
        except Exception as exc:
            log.error("  Failed: %s", exc)
            errors += 1
            # Mark as checked so we don't retry forever on deleted venues
            checked.add(venue_id)
            continue
        time.sleep(SLEEP)

        # Determine category
        if details["liked"]:
            json_key = "venueLikes"
        elif venue_id in dislike_ids:
            json_key = "venueDislikes"
        else:
            json_key = "venueOkays"

        log.info("  → %s (%s)", json_key, details["name"])

        # Remove from all lists first (venue may have moved)
        for key in ("venueLikes", "venueOkays", "venueDislikes"):
            before = len(ratings[key])
            ratings[key] = [e for e in ratings[key] if e.get("id") != venue_id]

        # Preserve existing entry fields (e.g. createdAt)
        existing_entry = next(
            (e for key in ("venueLikes", "venueOkays", "venueDislikes")
             for e in [next((x for x in load_ratings(out_path).get(key, []) if x.get("id") == venue_id), None)]
             if e),
            None,
        )

        new_entry = {
            "id":        venue_id,
            "name":      details["name"],
            "url":       details["url"],
            "createdAt": 0,
        }
        if existing_entry:
            new_entry = {**existing_entry, **new_entry}

        ratings[json_key].append(new_entry)
        checked.add(venue_id)
        rated_ids.add(venue_id)
        total_changed += 1

        # Save progress incrementally
        if total_changed % args.batch_size == 0:
            save_ratings(out_path, ratings)
            save_checked(checked_path, checked)
            log.info("  Progress saved (%d processed)", total_changed)

    # Final save
    if total_changed > 0:
        save_ratings(out_path, ratings)
        save_checked(checked_path, checked)
        log.info("Done. Updated %d venues (%d errors).", total_changed, errors)
    else:
        log.info("No changes made.")

    save_checked(checked_path, checked)
    print(f"CHANGED={'true' if total_changed > 0 else 'false'}")


if __name__ == "__main__":
    main()
