"""
fetch_tips_from_log.py — Parse scan.log for discovered tip IDs and fetch them.

Usage:
    python scripts/fetch_tips_from_log.py \
        --token "$TOKEN" \
        --log scan.log \
        --tips data/tips.json
"""
from __future__ import annotations
import argparse, json, re, time
from pathlib import Path
import requests

TIP_API  = "https://api.foursquare.com/v2/tips/{tid}"
API_V    = "20231201"
USER_ID  = "29447180"
SLEEP    = 0.25


def parse_tip_ids(log_path: Path) -> set[str]:
    ids: set[str] = set()
    for line in log_path.read_text(encoding="utf-16").splitlines():
        if "tip ID(s):" in line:
            for m in re.findall(r"[0-9a-f]{24}", line):
                ids.add(m)
    return ids


def fetch_tip(token: str, tip_id: str) -> dict | None:
    try:
        resp = requests.get(TIP_API.format(tid=tip_id),
                            params={"oauth_token": token, "v": API_V}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("meta", {}).get("code") != 200:
            return None
        return data.get("response", {}).get("tip")
    except Exception as e:
        print(f"  SKIP {tip_id}: {e}")
        return None


def tip_to_dict(t: dict) -> dict:
    venue   = t.get("venue") or {}
    loc     = venue.get("location") or {}
    cats    = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    lat, lng = loc.get("lat"), loc.get("lng")
    return {
        "id":             str(t.get("id") or ""),
        "ts":             int(t.get("createdAt") or 0),
        "text":           (t.get("text") or "").strip(),
        "venue":          (venue.get("name") or "").strip(),
        "venue_id":       str(venue.get("id") or ""),
        "city":           (loc.get("city") or "").strip(),
        "country":        (loc.get("country") or "").strip(),
        "lat":            round(float(lat), 5) if lat is not None else None,
        "lng":            round(float(lng), 5) if lng is not None else None,
        "category":       (primary.get("name") or "").strip(),
        "agree_count":    int(t.get("agreeCount") or 0),
        "disagree_count": int(t.get("disagreeCount") or 0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--log",   default="C:/Users/toouur/Documents/GitHub/foursquare-data/scan.log")
    parser.add_argument("--tips",  default="C:/Users/toouur/Documents/GitHub/foursquare-data/tips.json")
    args = parser.parse_args()

    log_path  = Path(args.log)
    tips_path = Path(args.tips)

    candidate_ids = parse_tip_ids(log_path)
    print(f"Found {len(candidate_ids)} candidate tip IDs in log")

    existing = json.loads(tips_path.read_text(encoding="utf-8")) if tips_path.exists() else []
    existing_ids = {t["id"] for t in existing if t.get("id")}

    new_tips: list[dict] = []
    for tid in sorted(candidate_ids):
        if tid in existing_ids:
            print(f"  SKIP {tid} (already in tips.json)")
            continue
        raw = fetch_tip(args.token, tid)
        if not raw:
            continue
        user = raw.get("user") or {}
        if str(user.get("id") or "") != USER_ID:
            print(f"  SKIP {tid} (belongs to user {user.get('id')}, not {USER_ID})")
            continue
        tip = tip_to_dict(raw)
        new_tips.append(tip)
        print(f"  + {tip['venue']} @ {tip['city']} — {tip['text'][:60]}")
        time.sleep(SLEEP)

    if not new_tips:
        print("No new tips to add.")
        return

    all_tips = sorted(
        {t["id"]: t for t in existing + new_tips}.values(),
        key=lambda t: -t.get("ts", 0),
    )
    tips_path.write_text(json.dumps(all_tips, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(all_tips)} tips → {tips_path}  (+{len(new_tips)} new)")


if __name__ == "__main__":
    main()
