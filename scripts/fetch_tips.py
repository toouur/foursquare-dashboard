"""
fetch_tips.py  –  Fetch tips from Foursquare API and merge into tips.json.

Works for closed venues — the API still returns venue data after closure.

Usage:
    python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out data/tips.json
    python scripts/fetch_tips.py --full   # force full re-fetch

Modes:
  - Incremental (default when tips.json exists): fetch tips sorted by recent,
    stop as soon as we reach a tip already in the file (by timestamp).
  - Full (--full or tips.json missing): offset-paginate through all tips.

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

TIPS_API = "https://api.foursquare.com/v2/users/self/tips"
API_V    = "20231201"
LIMIT    = 500
SLEEP    = 0.35


def resolve_token(cli_token: str | None) -> str:
    cli = (cli_token or "").strip()
    if cli:
        return cli
    return os.environ.get("FOURSQUARE_TOKEN", "").strip()


def load_existing(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        return []


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


def _request(token: str, params: dict) -> dict:
    resp = requests.get(
        TIPS_API,
        params={"oauth_token": token, "v": API_V, **params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"API error: {data.get('meta')}")
    return data.get("response", {}).get("tips", {})


def fetch_incremental(token: str, after_ts: int) -> list[dict]:
    """Fetch tips sorted by recent, stop when we reach already-known timestamps."""
    new_tips: list[dict] = []
    offset = 0

    while True:
        data = _request(token, {"limit": LIMIT, "offset": offset, "sort": "recent"})
        items = data.get("items", [])
        if not items:
            break

        done = False
        for item in items:
            tip_ts = int(item.get("createdAt") or 0)
            if tip_ts <= after_ts:
                done = True
                break
            new_tips.append(api_tip_to_dict(item))

        log.info("Incremental: found %d new tip(s) so far", len(new_tips))
        if done or len(new_tips) >= data.get("count", 0):
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return new_tips


def fetch_full(token: str) -> list[dict]:
    """Fetch all tips via offset pagination."""
    tips: list[dict] = []
    offset = 0

    probe = _request(token, {"limit": 1})
    total = probe.get("count", 0)
    log.info("Full fetch: server reports %d tips", total)

    while True:
        data = _request(token, {"limit": LIMIT, "offset": offset})
        items = data.get("items", [])
        if not items:
            break
        tips.extend(api_tip_to_dict(t) for t in items)
        log.info("Full fetch: %d / %d tips", len(tips), total)
        if len(tips) >= total:
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return tips


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="Foursquare OAuth token")
    parser.add_argument("--out",   default="data/tips.json", help="Output JSON path")
    parser.add_argument("--full",  action="store_true",      help="Force full re-fetch")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    out_path = Path(args.out)
    existing = load_existing(out_path)
    existing_ids = {t["id"] for t in existing if t.get("id")}
    max_ts = max((t.get("ts", 0) for t in existing), default=0)
    do_full = args.full or not out_path.exists() or not existing

    try:
        if do_full:
            log.info("Mode: FULL %s", "(forced)" if args.full else "(tips.json missing/empty)")
            fetched = fetch_full(token)
        else:
            log.info("Mode: INCREMENTAL (latest ts=%d)", max_ts)
            fetched = fetch_incremental(token, max_ts)
    except Exception as exc:
        log.error("Failed to fetch tips: %s", exc)
        print("CHANGED=false")
        return

    added = [t for t in fetched if t.get("id") not in existing_ids]
    log.info("New (de-duped): %d", len(added))

    if not added and not do_full:
        print("CHANGED=false")
        return

    if do_full:
        # Full re-fetch: replace entirely (dedupe by id)
        by_id = {t["id"]: t for t in fetched if t.get("id")}
        all_tips = sorted(by_id.values(), key=lambda t: -t["ts"])
        changed = all_tips != existing
    else:
        all_tips = sorted(existing + added, key=lambda t: -t.get("ts", 0))
        changed = True

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(all_tips, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Wrote %d tips → %s", len(all_tips), out_path)

    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
