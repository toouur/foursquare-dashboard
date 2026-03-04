"""
Foursquare Check-in Downloader
================================
Works both locally and in GitHub Actions.

Local (full fetch):    uses offset pagination — fast, no limits
GitHub Actions (full): uses beforeTimestamp pagination — bypasses 31,500 limit
Both (incremental):    fetches only new check-ins since last CSV, then merges

Columns saved:
  date, venue, venue_id, venue_url, city, state, country, neighborhood,
  lat, lng, address, category, shout, source_app, source_url,
  with_name, with_id

Usage:
  python foursquare_checkins.py            # auto: incremental if CSV exists, else full
  python foursquare_checkins.py --full     # force full re-fetch
  python foursquare_checkins.py --dry-run  # show what would happen, fetch nothing

Environment:
  FOURSQUARE_TOKEN   your OAuth token (required)
  CI / GITHUB_ACTIONS  set automatically by GitHub — switches to timestamp pagination
"""

import requests, csv, json, time, os, sys
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────
TOKEN       = os.environ.get("FOURSQUARE_TOKEN", "")
API_VERSION = "20231010"
BASE_URL    = "https://api.foursquare.com/v2"
LIMIT       = 250       # API max per request
SLEEP       = 0.4       # seconds between requests
OUTPUT_CSV  = "checkins.csv"
OUTPUT_JSON = "checkins.json"

# Detect GitHub Actions / any CI environment
IS_CI = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))

FIELDS = [
    "date", "venue", "venue_id", "venue_url",
    "city", "state", "country", "neighborhood",
    "lat", "lng", "address", "category", "shout",
    "source_app", "source_url", "with_name", "with_id",
]

# ── Parse a single API check-in object ───────────────────────────

def parse(c):
    venue  = c.get("venue", {})
    loc    = venue.get("location", {})
    cats   = venue.get("categories", [])
    source = c.get("source", {})
    companions = c.get("with", [])
    with_names = ", ".join(
        (p.get("firstName","") + " " + p.get("lastName","")).strip()
        for p in companions
    ).strip()
    with_ids = ", ".join(p.get("id","") for p in companions)
    vid = venue.get("id","")
    return {
        "date":         c.get("createdAt", ""),
        "venue":        venue.get("name", ""),
        "venue_id":     vid,
        "venue_url":    f"https://foursquare.com/v/{vid}" if vid else "",
        "city":         loc.get("city", ""),
        "state":        loc.get("state", ""),
        "country":      loc.get("country", ""),
        "neighborhood": loc.get("neighborhood", ""),
        "lat":          loc.get("lat", ""),
        "lng":          loc.get("lng", ""),
        "address":      loc.get("address", ""),
        "category":     cats[0].get("name","") if cats else "",
        "shout":        c.get("shout", ""),
        "source_app":   source.get("name", ""),
        "source_url":   source.get("url", ""),
        "with_name":    with_names,
        "with_id":      with_ids,
    }

# ── API call with 500-error retry ─────────────────────────────────

def api_get(params, retries=120):
    """
    GET /users/self/checkins with retry logic.
    On 500 errors during timestamp pagination, nudges 1 second at a time
    to skip corrupted windows while losing as few check-ins as possible.
    """
    for attempt in range(retries):
        resp = requests.get(
            f"{BASE_URL}/users/self/checkins",
            params={"oauth_token": TOKEN, "v": API_VERSION, **params}
        )
        if resp.status_code == 500 and "beforeTimestamp" in params:
            params["beforeTimestamp"] -= 1
            if attempt % 10 == 0:
                ts = params["beforeTimestamp"]
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                print(f"  ⚠  500 at {dt} (ts={ts}), nudging 1s back (attempt {attempt+1})...")
            time.sleep(1)
            continue
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            raise RuntimeError(f"API error: {data.get('meta')}")
        return data["response"]["checkins"]
    raise RuntimeError("Too many 500 errors from Foursquare API. Try again later.")

# ── Full fetch — two strategies ───────────────────────────────────

def fetch_full_offset():
    """
    Offset-based pagination.
    Fast and simple, but hits a hard 31,500 limit on GitHub Actions
    (likely due to API rate limiting in cloud environments).
    Use locally only.
    """
    all_items = []
    offset = 0

    first = api_get({"limit": 1})
    total = first["count"]
    print(f"  Server total: {total:,}  |  Strategy: offset (local)")

    while True:
        data  = api_get({"limit": LIMIT, "offset": offset})
        items = data["items"]
        if not items:
            break
        all_items.extend(items)
        print(f"  {len(all_items):,} / {total:,}")
        if len(all_items) >= total:
            break
        offset += LIMIT
        time.sleep(SLEEP)

    return all_items

def fetch_full_timestamp():
    """
    beforeTimestamp-based pagination.
    Works around the 31,500 offset limit in GitHub Actions.
    Includes auto-retry for 500 errors on corrupted timestamps.
    """
    all_items = []
    before_ts = None

    first = api_get({"limit": 1})
    total = first["count"]
    print(f"  Server total: {total:,}  |  Strategy: timestamp (CI)")

    while True:
        params = {"limit": LIMIT}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        data  = api_get(params)
        items = data["items"]
        if not items:
            break

        all_items.extend(items)
        oldest_ts = items[-1]["createdAt"]
        oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  {len(all_items):,} / {total:,}  (oldest: {oldest_dt})")

        if len(all_items) >= total:
            break

        before_ts = oldest_ts - 1
        time.sleep(SLEEP)

    return all_items

# ── Incremental fetch ─────────────────────────────────────────────

def fetch_incremental(after_ts):
    """
    Fetch only check-ins newer than after_ts.
    Uses afterTimestamp — always works in both environments
    since new check-ins are very few and never hit the offset limit.
    """
    after_dt = datetime.fromtimestamp(after_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"  Fetching new check-ins after {after_dt}...")

    new_items = []
    before_ts = None

    while True:
        params = {"limit": LIMIT, "afterTimestamp": after_ts}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        data      = api_get(params)
        items     = data["items"]
        total_new = data["count"]

        if not items:
            break

        new_items.extend(items)
        print(f"  {len(new_items):,} / {total_new:,} new check-ins")

        if len(new_items) >= total_new:
            break

        before_ts = items[-1]["createdAt"] - 1
        time.sleep(SLEEP)

    return new_items

# ── CSV helpers ───────────────────────────────────────────────────

def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved → {path}  ({len(rows):,} rows)")

def save_json(items, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    size = os.path.getsize(path) // 1024
    print(f"  Saved → {path}  ({size:,} KB)")

def get_latest_ts(path):
    """Returns unix timestamp of the newest row in existing CSV, or None."""
    if not os.path.exists(path):
        return None
    rows = load_csv(path)
    if not rows:
        return None
    try:
        return max(int(r["date"]) for r in rows if r.get("date","").strip())
    except ValueError:
        return None

def merge(existing_rows, new_items):
    """
    Merge new API items into existing CSV rows.
    Deduplicates by (venue_id, date). Sorts newest first.
    Returns (merged_rows, added_count).
    """
    existing_keys = set(
        (r.get("venue_id",""), str(r.get("date","")))
        for r in existing_rows
    )
    added = 0
    for item in new_items:
        row = parse(item)
        key = (row.get("venue_id",""), str(row["date"]))
        if key not in existing_keys:
            existing_rows.append(row)
            existing_keys.add(key)
            added += 1

    existing_rows.sort(key=lambda r: int(r.get("date") or 0), reverse=True)
    return existing_rows, added

# ── Main ──────────────────────────────────────────────────────────

def main():
    force_full = "--full"    in sys.argv
    dry_run    = "--dry-run" in sys.argv

    if not TOKEN:
        print("ERROR: FOURSQUARE_TOKEN not set.")
        print("  Windows CMD:   set FOURSQUARE_TOKEN=your_token")
        print("  PowerShell:    $env:FOURSQUARE_TOKEN='your_token'")
        print("  Mac/Linux:     export FOURSQUARE_TOKEN=your_token")
        print("  GitHub Actions: add it as a repository secret")
        sys.exit(1)

    env_label = "GitHub Actions" if IS_CI else "local"
    print(f"Environment: {env_label}")

    latest_ts = get_latest_ts(OUTPUT_CSV)

    # ── Decide mode ───────────────────────────────────────────────
    if force_full or latest_ts is None:
        if force_full:
            print("Mode: FULL (forced)")
        else:
            print(f"Mode: FULL ({OUTPUT_CSV} not found)")

        if dry_run:
            print("[dry-run] Would fetch all check-ins and write CSV + JSON.")
            return

        print("Fetching...")
        if IS_CI:
            items = fetch_full_timestamp()
        else:
            items = fetch_full_offset()

        rows = [parse(c) for c in items]
        rows.sort(key=lambda r: int(r.get("date") or 0), reverse=True)
        save_csv(rows, OUTPUT_CSV)
        save_json(items, OUTPUT_JSON)
        print(f"\n✓ Done! {len(rows):,} check-ins saved.")

    else:
        latest_dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"Mode: INCREMENTAL (latest in CSV: {latest_dt})")

        if dry_run:
            print(f"[dry-run] Would fetch check-ins after {latest_dt} and merge into CSV.")
            return

        print("Fetching...")
        new_items = fetch_incremental(latest_ts)

        if not new_items:
            print("\n✓ Already up to date — no new check-ins.")
            return

        existing = load_csv(OUTPUT_CSV)
        merged, added = merge(existing, new_items)
        print(f"\n  +{added:,} new  →  {len(merged):,} total")
        save_csv(merged, OUTPUT_CSV)
        save_json(new_items, OUTPUT_JSON)   # JSON = only new ones
        print(f"\n✓ Done!")


if __name__ == "__main__":
    main()