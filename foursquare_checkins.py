"""
Foursquare Check-in History Downloader
Fetches all check-ins and saves to checkins.csv + checkins.json
"""
import requests, json, csv, time, os
from datetime import datetime, timezone

TOKEN       = os.environ.get("FOURSQUARE_TOKEN", "")
API_VERSION = "20231010"
BASE_URL    = "https://api.foursquare.com/v2"
LIMIT       = 250
SLEEP       = 0.4
OUTPUT_CSV  = "checkins.csv"
OUTPUT_JSON = "checkins.json"

def fetch_checkins():
    all_checkins = []
    offset = 0
    print("Fetching check-in history...")
    while True:
        resp = requests.get(f"{BASE_URL}/users/self/checkins", params={
            "oauth_token": TOKEN, "v": API_VERSION,
            "limit": LIMIT, "offset": offset,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            print("API error:", data.get("meta")); break
        checkins = data["response"]["checkins"]
        total, items = checkins["count"], checkins["items"]
        all_checkins.extend(items)
        print(f"  {len(all_checkins):,} / {total:,}")
        if len(all_checkins) >= total or not items:
            break
        offset += LIMIT
        time.sleep(SLEEP)
    return all_checkins

def parse_checkin(c):
    venue = c.get("venue", {})
    loc   = venue.get("location", {})
    ts    = c.get("createdAt", 0)
    return {
        "date":     ts,
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

def save_csv(checkins):
    fields = ["date","venue","city","country","lat","lng","shout","category","venue_id","address"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in checkins:
            w.writerow(parse_checkin(c))
    print(f"Saved → {OUTPUT_CSV} ({len(checkins):,} rows)")

def save_json(checkins):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(checkins, f, ensure_ascii=False, indent=2)
    print(f"Saved → {OUTPUT_JSON}")

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: Set FOURSQUARE_TOKEN environment variable"); exit(1)
    checkins = fetch_checkins()
    save_csv(checkins)
    save_json(checkins)
    print(f"\nDone! {len(checkins):,} check-ins.")
