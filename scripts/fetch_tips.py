"""
fetch_tips.py  –  Fetch tips from Foursquare API and merge into tips.json.

Two complementary strategies to capture all tips including those on closed venues:

  1. /users/self/tips  — fast, returns ~all tips, but may miss some closed-venue tips.
  2. /venues/{vid}/tips?filter=self  — per-venue sweep using venue IDs from checkins.csv.
     Only queries venues NOT already in the tip set. Catches tips the user endpoint omits.

Usage:
    python scripts/fetch_tips.py --token "$FOURSQUARE_TOKEN" --out data/tips.json
    python scripts/fetch_tips.py --full              # force full re-fetch (users endpoint)
    python scripts/fetch_tips.py --full --sweep      # full re-fetch + venue sweep
    python scripts/fetch_tips.py --sweep             # venue sweep only (extend existing tips)
    python scripts/fetch_tips.py --csv data/checkins.csv --sweep  # explicit CSV path
    python scripts/fetch_tips.py --add-tip-id 645265a53112b8775c114ecb  # fetch one tip by ID (marks closed=True)

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

TIPS_API   = "https://api.foursquare.com/v2/users/self/tips"
VENUE_API  = "https://api.foursquare.com/v2/venues/{vid}/tips"
TIP_API    = "https://api.foursquare.com/v2/tips/{tid}"
API_V      = "20231201"
LIMIT      = 500
SLEEP      = 0.35
SLEEP_VEN  = 0.25   # shorter sleep for per-venue calls (one tip each at most)


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
        "closed":         bool(venue.get("closed", False)),
    }


def _request_users(token: str, params: dict) -> dict:
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
        data = _request_users(token, {"limit": LIMIT, "offset": offset, "sort": "recent"})
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

    probe = _request_users(token, {"limit": 1})
    total = probe.get("count", 0)
    log.info("Full fetch: server reports %d tips", total)

    while True:
        data = _request_users(token, {"limit": LIMIT, "offset": offset})
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


def load_venue_ids_from_csv(csv_path: Path) -> list[str]:
    """Return unique non-empty venue_ids from checkins.csv, preserving first-seen order."""
    seen: dict[str, None] = {}
    try:
        with open(csv_path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                vid = (row.get("venue_id") or "").strip()
                if vid:
                    seen[vid] = None
    except Exception as exc:
        log.warning("Could not read CSV %s: %s", csv_path, exc)
    return list(seen)


def fetch_venue_sweep(token: str, venue_ids: list[str], known_ids: set[str]) -> list[dict]:
    """
    For each venue_id, call /venues/{vid}/tips?filter=self.
    Only queries venues whose tips are not already in known_ids (by venue_id).
    Returns any newly discovered tips.
    """
    # Build set of venue_ids already covered by existing tips
    covered_venues = known_ids  # we pass venue_ids of already-known tips

    candidates = [vid for vid in venue_ids if vid not in covered_venues]
    log.info("Venue sweep: %d venues to probe (skipping %d already covered)",
             len(candidates), len(venue_ids) - len(candidates))

    new_tips: list[dict] = []
    for i, vid in enumerate(candidates):
        try:
            resp = requests.get(
                VENUE_API.format(vid=vid),
                params={"oauth_token": token, "v": API_V, "limit": 200, "filter": "self"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("meta", {}).get("code") != 200:
                continue
            items = data.get("response", {}).get("tips", {}).get("items", [])
            for item in items:
                tip = api_tip_to_dict(item)
                if tip["id"] and tip["id"] not in known_ids:
                    new_tips.append(tip)
                    log.info("  Found hidden tip on venue %s: %s", vid, tip["text"][:60])
        except Exception as exc:
            log.debug("Venue %s skipped: %s", vid, exc)

        if (i + 1) % 100 == 0:
            log.info("Venue sweep progress: %d / %d probed, %d new tips found",
                     i + 1, len(candidates), len(new_tips))
        time.sleep(SLEEP_VEN)

    log.info("Venue sweep complete: %d new tips found across %d venues probed",
             len(new_tips), len(candidates))
    return new_tips


def fetch_tip_by_id(token: str, tip_id: str) -> dict | None:
    """Fetch a single tip via /v2/tips/{tipId} — free, no credit cost."""
    try:
        resp = requests.get(
            TIP_API.format(tid=tip_id),
            params={"oauth_token": token, "v": API_V},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            return None
        return data.get("response", {}).get("tip")
    except Exception as exc:
        log.debug("Failed to fetch tip %s: %s", tip_id, exc)
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token",      default="",               help="Foursquare OAuth token")
    parser.add_argument("--out",        default="data/tips.json", help="Output JSON path")
    parser.add_argument("--csv",        default="",               help="checkins.csv path for venue sweep")
    parser.add_argument("--full",       action="store_true",      help="Force full re-fetch via users endpoint")
    parser.add_argument("--sweep",      action="store_true",      help="Also sweep per-venue for hidden tips")
    parser.add_argument("--add-tip-id", default="",               help="Fetch a single tip by ID and add it as closed=True")
    args = parser.parse_args()

    token = resolve_token(args.token)
    if not token:
        log.error("Missing token. Provide --token or set FOURSQUARE_TOKEN.")
        print("CHANGED=false")
        return

    out_path = Path(args.out)
    existing = load_existing(out_path)
    existing_ids = {t["id"] for t in existing if t.get("id")}

    # ── --add-tip-id: fetch one tip by ID and add it as closed=True ──────────
    if args.add_tip_id:
        tip_id = args.add_tip_id.strip()
        if tip_id in existing_ids:
            log.info("Tip %s is already in tips.json — nothing to do.", tip_id)
            print("CHANGED=false")
            return
        raw = fetch_tip_by_id(token, tip_id)
        if not raw:
            log.error("Could not fetch tip %s — check the ID and token.", tip_id)
            print("CHANGED=false")
            return
        tip = api_tip_to_dict(raw)
        tip["closed"] = True
        by_id = {t["id"]: t for t in existing if t.get("id")}
        by_id[tip["id"]] = tip
        all_tips = sorted(by_id.values(), key=lambda t: -t.get("ts", 0))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(all_tips, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Added tip %s — %s @ %s", tip["id"], tip["venue"], tip["city"])
        print("CHANGED=true")
        return

    max_ts = max((t.get("ts", 0) for t in existing), default=0)
    do_full = args.full or not out_path.exists() or not existing

    # ── Step 1: users/self/tips ──────────────────────────────────────────────
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

    # Merge with existing
    by_id: dict[str, dict] = {t["id"]: t for t in existing if t.get("id")}
    for t in fetched:
        if t.get("id"):
            by_id[t["id"]] = t
    new_from_users = len(by_id) - len(existing_ids)

    # ── Step 2: venue sweep (optional) ───────────────────────────────────────
    new_from_sweep = 0
    if args.sweep:
        # Resolve CSV path: explicit --csv, or next to tips.json, or default
        if args.csv:
            csv_path = Path(args.csv)
        else:
            csv_path = out_path.parent / "checkins.csv"
        if not csv_path.exists():
            log.warning("--sweep requested but CSV not found at %s", csv_path)
        else:
            venue_ids = load_venue_ids_from_csv(csv_path)
            # "covered" = venues already represented in the merged tip set
            covered_venue_ids = {t["venue_id"] for t in by_id.values() if t.get("venue_id")}
            sweep_tips = fetch_venue_sweep(token, venue_ids, covered_venue_ids)
            for t in sweep_tips:
                if t.get("id") and t["id"] not in by_id:
                    t["closed"] = True   # sweep-only tips are on closed venues
                    by_id[t["id"]] = t
                    new_from_sweep += 1

    all_tips = sorted(by_id.values(), key=lambda t: -t.get("ts", 0))
    changed = len(all_tips) != len(existing) or new_from_users > 0 or new_from_sweep > 0

    # Always do a content comparison so manual patches to existing tips
    # (e.g. setting closed=True) are detected and trigger a rebuild.
    if not changed:
        changed = all_tips != existing

    if changed:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(all_tips, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Wrote %d tips → %s (+%d from users endpoint, +%d from venue sweep)",
                 len(all_tips), out_path, new_from_users, new_from_sweep)
    else:
        log.info("No new tips found.")

    print(f"CHANGED={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
