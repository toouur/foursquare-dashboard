# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
refresh_backfill_venue.py — Fill in venue metadata (name, city, country, lat,
lng, address, category) for reconstructed rows in backfill.csv by fetching the
venue directly from Foursquare.

Unlike refresh_venue.py (which reads the *free* /users/self/checkins endpoint and
therefore only works for venues you actually checked into), backfill venues are
pre-2012 / historical places with no real check-in, so this uses the direct
/v2/venues/{id} endpoint. That endpoint draws on the premium call budget, so a
402 simply means the monthly quota is spent — the row is left untouched (its
`[coords_approx:pending_api]` marker stays) and can be retried after the reset.

Targets rows whose venue_id is set but coords are still placeholders — either the
explicit --venue-ids list, or (with --all-pending) every row whose shout still
carries the `[coords_approx:pending_api]` marker.

Per matching row it updates: venue, venue_url, city, state, country,
neighborhood, lat, lng, address, category — and strips the
`[coords_approx:pending_api]` marker from the shout. It never touches date,
source_app, source_url, companions, city_inferred or checkin_id.

Usage:
    python scripts/refresh_backfill_venue.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --backfill private-data/backfill.csv \\
        --venue-ids 4c7be6e0566db60c11164c0e,4d502fd1c5ff6ea80d1d9807

    python scripts/refresh_backfill_venue.py \\
        --token "$FOURSQUARE_TOKEN" \\
        --backfill private-data/backfill.csv --all-pending --dry-run
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

VENUE_API = "https://api.foursquare.com/v2/venues/{}"
API_V = "20231201"
PENDING_MARKER = "[coords_approx:pending_api]"

# Location fields patched from Foursquare (venue_id / venue_url handled separately).
LOC_FIELDS = ("city", "state", "country", "neighborhood", "lat", "lng", "address", "category")


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    return cli or os.environ.get("FOURSQUARE_TOKEN", "").strip()


def fetch_venue(token: str, venue_id: str) -> dict | None:
    """GET /v2/venues/{id}. Returns the venue object, or None on error/quota."""
    try:
        resp = requests.get(
            VENUE_API.format(venue_id),
            params={"oauth_token": token, "v": API_V},
            timeout=30,
        )
    except Exception as exc:
        log.warning("venue %s: request failed: %s", venue_id, exc)
        return None
    if resp.status_code == 402:
        log.error("venue %s: 402 Payment Required — premium quota spent, retry after reset.", venue_id)
        return None
    if resp.status_code != 200:
        log.warning("venue %s: HTTP %d — %s", venue_id, resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    if data.get("meta", {}).get("code") != 200:
        log.warning("venue %s: API meta %s", venue_id, data.get("meta"))
        return None
    return data.get("response", {}).get("venue")


def venue_to_patch(venue: dict) -> dict:
    """Foursquare venue object -> dict of backfill.csv fields to write."""
    loc = venue.get("location") or {}
    cats = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    lat, lng = loc.get("lat"), loc.get("lng")
    return {
        "venue":        (venue.get("name") or "").strip(),
        "city":         (loc.get("city") or "").strip(),
        "state":        (loc.get("state") or "").strip(),
        "country":      (loc.get("country") or "").strip(),
        "neighborhood": (loc.get("neighborhood") or "").strip(),
        "lat":          str(lat) if lat is not None else "",
        "lng":          str(lng) if lng is not None else "",
        "address":      (loc.get("address") or "").strip(),
        "category":     (primary.get("name") or "").strip(),
    }


def strip_marker(shout: str) -> str:
    """Drop the pending marker, keeping any other bracket tags (e.g. [date_approx])."""
    out = shout.replace(PENDING_MARKER, "")
    return re.sub(r"\s{2,}", " ", out).strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill backfill.csv venue coords from Foursquare")
    ap.add_argument("--token", default="", help="Foursquare OAuth token (or $FOURSQUARE_TOKEN)")
    ap.add_argument("--backfill", default="private-data/backfill.csv", help="Path to backfill.csv")
    ap.add_argument("--venue-ids", default="", help="Comma-separated venue IDs to refresh")
    ap.add_argument("--all-pending", action="store_true",
                    help="Refresh every row whose shout still has the pending marker")
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = ap.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        raise SystemExit(2)

    path = Path(args.backfill)
    if not path.exists():
        log.error("backfill.csv not found: %s", path)
        raise SystemExit(2)

    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    # Which venue IDs to refresh.
    ids: list[str] = []
    if args.venue_ids.strip():
        ids = [v.strip() for v in args.venue_ids.split(",") if v.strip()]
    elif args.all_pending:
        seen: set[str] = set()
        for r in rows:
            vid = (r.get("venue_id") or "").strip()
            if vid and PENDING_MARKER in (r.get("shout") or "") and vid not in seen:
                seen.add(vid)
                ids.append(vid)
    else:
        log.error("Pass --venue-ids or --all-pending.")
        raise SystemExit(2)

    if not ids:
        log.info("No pending venue IDs to refresh — nothing to do.")
        return
    log.info("Refreshing %d venue(s): %s", len(ids), ", ".join(ids))

    total_changed = 0
    ok, failed = 0, 0
    for vid in ids:
        venue = fetch_venue(token, vid)
        if not venue:
            failed += 1
            continue
        patch = venue_to_patch(venue)
        log.info("venue %s -> %s | %s, %s | %s,%s",
                 vid, patch["venue"], patch["city"], patch["country"], patch["lat"], patch["lng"])
        matched = 0
        for row in rows:
            if (row.get("venue_id") or "").strip() != vid:
                continue
            matched += 1
            row["venue"] = patch["venue"]
            row["venue_url"] = f"https://foursquare.com/v/{vid}"
            for f in LOC_FIELDS:
                row[f] = patch[f]
            row["shout"] = strip_marker(row.get("shout") or "")
        if matched:
            ok += 1
            total_changed += matched
            log.info("  patched %d row(s) for venue %s", matched, vid)
        else:
            log.warning("  no backfill rows carry venue_id %s", vid)

    log.info("Done: %d venue(s) ok, %d failed; %d row(s) changed.", ok, failed, total_changed)

    if args.dry_run:
        log.info("Dry run — backfill.csv not written.")
        return
    if total_changed == 0:
        log.info("Nothing changed — backfill.csv not written.")
        return
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("Saved %s (%d row(s) updated).", path, total_changed)


if __name__ == "__main__":
    main()
