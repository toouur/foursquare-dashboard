"""
fetch_tips.py  –  Fetch all tips from Foursquare API and save to data/tips.json.

Works for closed venues — the API still returns venue data even if the venue
has since closed.

Usage:
    python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN"
    python scripts/fetch_tips.py  # uses FOURSQUARE_TOKEN env var

Outputs data/tips.json (or --out path), sorted newest-first.
Prints TIPS_COUNT=N to stdout.
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

TIPS_API = "https://api.foursquare.com/v2/users/self/tips"
API_V    = "20231201"
LIMIT    = 500
SLEEP    = 0.35


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def api_tip_to_dict(t: dict) -> dict:
    venue   = t.get("venue") or {}
    loc     = venue.get("location") or {}
    cats    = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    vid     = str(venue.get("id") or "")
    tid     = str(t.get("id") or "")
    lat     = loc.get("lat")
    lng     = loc.get("lng")
    return {
        "id":             tid,
        "ts":             int(t.get("createdAt") or 0),
        "text":           (t.get("text") or "").strip(),
        "venue":          (venue.get("name") or "").strip(),
        "venue_id":       vid,
        "city":           (loc.get("city") or "").strip(),
        "country":        (loc.get("country") or "").strip(),
        "lat":            round(float(lat), 5) if lat is not None else None,
        "lng":            round(float(lng), 5) if lng is not None else None,
        "category":       (primary.get("name") or "").strip(),
        "agree_count":    int(t.get("agreeCount") or 0),
        "disagree_count": int(t.get("disagreeCount") or 0),
    }


def fetch_all(token: str) -> list[dict]:
    tips: list[dict] = []
    offset = 0

    probe = requests.get(
        TIPS_API,
        params={"oauth_token": token, "v": API_V, "limit": 1},
        timeout=30,
    )
    probe.raise_for_status()
    total = probe.json().get("response", {}).get("tips", {}).get("count", 0)
    log.info("Server reports %d tips", total)

    while True:
        resp = requests.get(
            TIPS_API,
            params={"oauth_token": token, "v": API_V, "limit": LIMIT, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            raise RuntimeError(f"API error: {data.get('meta')}")
        items = data.get("response", {}).get("tips", {}).get("items", [])
        if not items:
            break
        tips.extend(api_tip_to_dict(t) for t in items)
        log.info("Fetched %d / %d tips", len(tips), total)
        if len(tips) >= total:
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return tips


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--out",   default="data/tips.json", help="Output JSON path")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("TIPS_COUNT=0")
        return

    try:
        tips = fetch_all(token)
    except Exception as exc:
        log.error("Failed to fetch tips: %s", exc)
        print("TIPS_COUNT=0")
        return

    tips.sort(key=lambda t: -t["ts"])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(tips, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Wrote %d tips → %s", len(tips), out_path)
    print(f"TIPS_COUNT={len(tips)}")


if __name__ == "__main__":
    main()
