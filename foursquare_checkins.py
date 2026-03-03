"""
Foursquare Check-in Downloader
-------------------------------
- Full fetch: uses beforeTimestamp pagination to bypass the 31,500 offset limit
- Incremental: if checkins.csv already exists, only fetches new check-ins and merges

Usage:
  export FOURSQUARE_TOKEN=your_token
  python foursquare_checkins.py           # auto-detects full vs incremental
  python foursquare_checkins.py --full    # force full re-fetch
"""

import requests, json, csv, time, os, sys
from datetime import datetime, timezone

TOKEN       = os.environ.get("FOURSQUARE_TOKEN", "")
API_VERSION = "20231010"
BASE_URL    = "https://api.foursquare.com/v2"
LIMIT       = 250
SLEEP       = 0.4
OUTPUT_CSV  = "checkins.csv"
OUTPUT_JSON = "checkins.json"

FIELDS = ["date","venue","city","country","lat","lng","shout","category","venue_id","address"]

# ── Helpers ───────────────────────────────────────────────────────

def parse_checkin(c):
    venue = c.get("venue", {})
    loc   = venue.get("location", {})
    return {
        "date":     c.get("createdAt", 0),
        "venue":    venue.get("name", ""),
        "city":     loc.get("city", ""),
        "country":  loc.get("country", ""),
        "lat":      loc.get("lat", ""),
        "lng":      loc.get("lng", ""),
        "shout":    c.get("shout", ""),
        "category": venue.get("categories", [{}])[0].get("name", "") if venue.get("categories") else "",
        "venue_id": venue.get("id", ""),
        "address":  loc.get("address", ""),
    }

def api_get(params):
    resp = requests.get(f"{BASE_URL}/users/self/checkins", params={
        "oauth_token": TOKEN, "v": API_VERSION, **params
    })
    resp.raise_for_status()
    data = resp.json()
    if data.get("meta", {}).get("code") != 200:
        raise Exception(f"API error: {data.get('meta')}")
    return data["response"]["checkins"]

def save_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Saved → {path}  ({len(rows):,} rows)")

def save_json(checkins, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(checkins, f, ensure_ascii=False, indent=2)
    print(f"Saved → {path}")

def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def get_latest_timestamp(csv_path):
    """Returns unix timestamp of the newest check-in in existing CSV."""
    if not os.path.exists(csv_path):
        return None
    rows = load_csv(csv_path)
    if not rows:
        return None
    return max(int(r["date"]) for r in rows if r["date"])

# ── Fetch strategies ──────────────────────────────────────────────

def fetch_full():
    """
    Fetch ALL check-ins using beforeTimestamp pagination.
    This bypasses the 31,500 offset hard limit.
    """
    all_checkins = []
    before_ts = None

    # Get total count first
    first = api_get({"limit": 1})
    total = first["count"]
    print(f"Total check-ins on server: {total:,}")
    print("Fetching all (using timestamp pagination)...")

    while True:
        params = {"limit": LIMIT}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        data = api_get(params)
        items = data["items"]
        if not items:
            break

        all_checkins.extend(items)
        oldest_ts = items[-1]["createdAt"]
        oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  {len(all_checkins):,} / {total:,}  (oldest so far: {oldest_dt})")

        if len(all_checkins) >= total:
            break

        before_ts = oldest_ts - 1
        time.sleep(SLEEP)

    return all_checkins

def fetch_incremental(after_ts):
    """
    Fetch only check-ins newer than after_ts.
    Uses afterTimestamp so only new ones come back.
    """
    new_checkins = []
    after_dt = datetime.fromtimestamp(after_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Incremental mode — fetching check-ins after {after_dt}...")

    # Use beforeTimestamp pagination within the afterTimestamp window
    before_ts = None
    while True:
        params = {"limit": LIMIT, "afterTimestamp": after_ts}
        if before_ts:
            params["beforeTimestamp"] = before_ts

        data = api_get(params)
        items = data["items"]
        total_new = data["count"]

        if not items:
            break

        new_checkins.extend(items)
        print(f"  {len(new_checkins):,} / {total_new:,} new check-ins found")

        if len(new_checkins) >= total_new:
            break

        before_ts = items[-1]["createdAt"] - 1
        time.sleep(SLEEP)

    return new_checkins

# ── Merge & deduplicate ───────────────────────────────────────────

def merge(existing_rows, new_checkins):
    """
    Merge new API results into existing CSV rows.
    Deduplicates by venue_id+date, sorts newest first.
    """
    existing_ids = set(
        (r["venue_id"], r["date"]) for r in existing_rows
    )
    added = 0
    for c in new_checkins:
        parsed = parse_checkin(c)
        key = (parsed["venue_id"], str(parsed["date"]))
        if key not in existing_ids:
            existing_rows.append(parsed)
            existing_ids.add(key)
            added += 1

    # Sort newest first (descending by date)
    existing_rows.sort(key=lambda r: int(r["date"]), reverse=True)
    return existing_rows, added

# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: Set FOURSQUARE_TOKEN environment variable")
        print("  Windows CMD:   set FOURSQUARE_TOKEN=your_token")
        print("  PowerShell:    $env:FOURSQUARE_TOKEN='your_token'")
        print("  Mac/Linux:     export FOURSQUARE_TOKEN=your_token")
        sys.exit(1)

    force_full = "--full" in sys.argv
    latest_ts  = get_latest_timestamp(OUTPUT_CSV)

    if force_full or latest_ts is None:
        # ── Full fetch ────────────────────────────────────────────
        if force_full:
            print("Forced full re-fetch...")
        else:
            print(f"{OUTPUT_CSV} not found — running full fetch...")

        checkins = fetch_full()
        rows = [parse_checkin(c) for c in checkins]
        rows.sort(key=lambda r: int(r["date"]), reverse=True)
        save_csv(rows, OUTPUT_CSV)
        save_json(checkins, OUTPUT_JSON)
        print(f"\n✓ Done! {len(rows):,} check-ins saved.")

    else:
        # ── Incremental fetch ─────────────────────────────────────
        latest_dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"Found existing {OUTPUT_CSV} — latest check-in: {latest_dt}")

        new_checkins = fetch_incremental(latest_ts)

        if not new_checkins:
            print("\n✓ Already up to date — no new check-ins found.")
            sys.exit(0)

        existing_rows = load_csv(OUTPUT_CSV)
        merged, added = merge(existing_rows, new_checkins)

        print(f"\n  {added:,} new check-in(s) added  →  {len(merged):,} total")
        save_csv(merged, OUTPUT_CSV)
        save_json(new_checkins, OUTPUT_JSON)  # only save new ones to JSON
        print(f"\n✓ Done! Dashboard will reflect {len(merged):,} total check-ins.")