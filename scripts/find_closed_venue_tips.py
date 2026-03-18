"""
find_closed_venue_tips.py — Discover tips on closed/deleted venues.

Fetches app.foursquare.com venue pages using your browser session cookies
to find tip IDs not returned by /users/self/tips (closed venues are omitted
by that endpoint), then fetches full tip data via the free /v2/tips/{tipId}.

Setup:
  1. Install "Cookie-Editor" browser extension
  2. Go to foursquare.com (logged in), open Cookie-Editor → Export → Netscape
  3. Save to e.g. cookies.txt
  4. Run:
       python scripts/find_closed_venue_tips.py \
         --token "$FOURSQUARE_TOKEN" \
         --cookies cookies.txt \
         --csv data/checkins.csv \
         --tips data/tips.json

This is a one-time local script, not part of CI.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

TIP_API   = "https://api.foursquare.com/v2/tips/{tid}"
VENUE_URL = "https://foursquare.com/v/{vid}"
API_V     = "20231201"
USER_ID   = "29447180"
WORKERS   = 20    # concurrent venue page fetches
SLEEP_API = 0.2   # between /v2/tips calls


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_venue_ids_from_csv(csv_path: Path) -> list[str]:
    seen: dict[str, None] = {}
    with open(csv_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            vid = (row.get("venue_id") or "").strip()
            if vid:
                seen[vid] = None
    return list(seen)


def load_tips(tips_path: Path) -> list[dict]:
    if not tips_path.exists():
        return []
    try:
        return json.loads(tips_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def extract_next_data(html: str) -> dict | None:
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def find_tip_ids_in_html(html: str, user_id: str) -> set[str]:
    """Find tip IDs rendered as static HTML near the user's profile link."""
    found: set[str] = set()
    # Pattern: 24-char hex tip ID appears within 300 chars before /user/{user_id}
    for m in re.finditer(rf'/user/{re.escape(user_id)}', html):
        snippet = html[max(0, m.start() - 300):m.start()]
        for tid in re.findall(r'[0-9a-f]{24}', snippet):
            found.add(tid)
    return found


def find_tip_ids_in_obj(data: object, user_id: str, found: set[str] | None = None) -> set[str]:
    """Recursively walk __NEXT_DATA__ and collect tip IDs belonging to user_id."""
    if found is None:
        found = set()
    if isinstance(data, dict):
        tip_id = str(data.get("id") or "")
        user   = data.get("user") or {}
        if (
            tip_id
            and data.get("text")
            and data.get("createdAt")
            and str(user.get("id") or "") == user_id
        ):
            found.add(tip_id)
        for v in data.values():
            find_tip_ids_in_obj(v, user_id, found)
    elif isinstance(data, list):
        for item in data:
            find_tip_ids_in_obj(item, user_id, found)
    return found


def api_tip_to_dict(t: dict) -> dict:
    venue   = t.get("venue") or {}
    loc     = venue.get("location") or {}
    cats    = venue.get("categories") or []
    primary = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    lat     = loc.get("lat")
    lng     = loc.get("lng")
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


def probe_venue(vid: str, session: requests.Session, existing_tip_ids: set[str]) -> set[str]:
    """Fetch one venue page and return any new tip IDs found."""
    try:
        resp = session.get(VENUE_URL.format(vid=vid), timeout=20, allow_redirects=True)
        if resp.status_code == 301 or resp.status_code == 302:
            resp = session.get(resp.headers["Location"], timeout=20, allow_redirects=True)
        if resp.status_code != 200:
            return set()
        html = resp.text
        next_data = extract_next_data(html)
        tip_ids = find_tip_ids_in_obj(next_data, USER_ID) if next_data else set()
        tip_ids |= find_tip_ids_in_html(html, USER_ID)
        return tip_ids - existing_tip_ids
    except Exception:
        return set()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Find tips on closed venues via browser cookies")
    parser.add_argument("--token",   required=True,         help="Foursquare OAuth token")
    parser.add_argument("--cookies", required=True,         help="Netscape cookie file (from Cookie-Editor)")
    parser.add_argument("--csv",     default="data/checkins.csv")
    parser.add_argument("--tips",    default="data/tips.json")
    parser.add_argument("--workers", type=int, default=WORKERS, help="Concurrent requests (default 20)")
    parser.add_argument("--dry-run", action="store_true",   help="Discover IDs only, don't write tips.json")
    parser.add_argument("--debug-venue", default="",        help="Fetch one venue ID and print raw __NEXT_DATA__ keys")
    args = parser.parse_args()

    csv_path  = Path(args.csv)
    tips_path = Path(args.tips)

    # ── Debug single venue ────────────────────────────────────────────────────
    if args.debug_venue:
        jar = MozillaCookieJar(args.cookies)
        jar.load(ignore_discard=True, ignore_expires=True)
        session = requests.Session()
        session.cookies = jar  # type: ignore[assignment]
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; personal-script)"})
        url  = VENUE_URL.format(vid=args.debug_venue)
        resp = session.get(url, timeout=20, allow_redirects=True)
        log.info("HTTP %d  final URL: %s", resp.status_code, resp.url)
        nd = extract_next_data(resp.text)
        html = resp.text
        nd = extract_next_data(html)
        tip_ids = find_tip_ids_in_obj(nd, USER_ID) if nd else set()
        tip_ids |= find_tip_ids_in_html(html, USER_ID)
        log.info("Tip IDs found for user %s: %s", USER_ID, tip_ids)
        return

    # ── Load existing data ────────────────────────────────────────────────────
    existing_tips    = load_tips(tips_path)
    covered_vids     = {t["venue_id"] for t in existing_tips if t.get("venue_id")}
    existing_tip_ids = {t["id"]       for t in existing_tips if t.get("id")}

    all_vids  = load_venue_ids_from_csv(csv_path)
    uncovered = [v for v in all_vids if v not in covered_vids]
    log.info("%d total venues | %d already have a tip | %d to probe (workers=%d)",
             len(all_vids), len(covered_vids), len(uncovered), args.workers)

    # ── Build shared session with browser cookies ─────────────────────────────
    jar = MozillaCookieJar(args.cookies)
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=args.workers,
        pool_maxsize=args.workers,
    )
    session.mount("https://", adapter)
    session.cookies = jar  # type: ignore[assignment]
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; personal-script)"})

    # ── Probe venue pages concurrently, fetch + save tips immediately ─────────
    discovered: set[str] = set()
    lock       = threading.Lock()
    done_count = 0
    saved_count = 0

    def save_tip(tip: dict) -> None:
        """Append one tip to tips.json immediately (thread-safe)."""
        nonlocal saved_count
        current = load_tips(tips_path)
        by_id = {t["id"]: t for t in current if t.get("id")}
        if tip["id"] in by_id:
            return
        by_id[tip["id"]] = tip
        all_sorted = sorted(by_id.values(), key=lambda t: -t.get("ts", 0))
        tips_path.write_text(json.dumps(all_sorted, ensure_ascii=False, indent=2), encoding="utf-8")
        saved_count += 1
        log.info("  SAVED %s @ %s — %.60s", tip["venue"], tip["city"], tip["text"])

    def task(vid: str) -> tuple[str, set[str]]:
        return vid, probe_venue(vid, session, existing_tip_ids)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(task, vid): vid for vid in uncovered}
        for future in as_completed(futures):
            vid, new_ids = future.result()
            with lock:
                done_count += 1
                fresh = new_ids - discovered - existing_tip_ids
                if fresh:
                    discovered.update(fresh)
                    log.info("Venue %s → tip ID(s): %s", vid, fresh)
                    if not args.dry_run:
                        for tid in fresh:
                            raw = fetch_tip_by_id(args.token, tid)
                            if raw:
                                user = raw.get("user") or {}
                                if str(user.get("id") or "") == USER_ID:
                                    tip = api_tip_to_dict(raw)
                                    if tip["id"]:
                                        save_tip(tip)
                                        existing_tip_ids.add(tip["id"])
                            time.sleep(SLEEP_API)
                if done_count % 500 == 0:
                    log.info("Progress: %d / %d probed | %d IDs found | %d tips saved",
                             done_count, len(uncovered), len(discovered), saved_count)

    log.info("Complete: %d venues probed | %d tip IDs found | %d tips saved",
             done_count, len(discovered), saved_count)

    if args.dry_run and discovered:
        log.info("Dry run — discovered IDs: %s", sorted(discovered))
    if not discovered:
        log.info("Nothing new found.")



if __name__ == "__main__":
    main()
