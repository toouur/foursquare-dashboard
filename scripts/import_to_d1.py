# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
import_to_d1.py — One-time full import of all Foursquare data into Cloudflare D1.

Usage (local, first run):
    export CF_D1_TOKEN=cfat_...
    python scripts/import_to_d1.py \
        --csv    /path/to/checkins.csv \
        --tips   /path/to/tips.json \
        --ratings /path/to/venueRatings.json \
        --lists  /path/to/lists.json \
        --schema scripts/d1_schema.sql

To skip tables already imported (if you hit the 100K writes/day free-tier limit):
    python scripts/import_to_d1.py ... --skip checkins venues
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import d1_client as d1

HERE = Path(__file__).parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "0", 0) else None
    except (ValueError, TypeError):
        return None


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_checkins(csv_path: str) -> tuple[list[list], dict]:
    """
    Returns (rows_for_checkins_table, venue_meta_dict).
    venue_meta keyed by venue_id: {name, category, lat, lng, city, country,
                                   first_ts, last_ts, count}
    """
    rows: list[list] = []
    venue_meta: dict = defaultdict(lambda: {
        "name": "", "category": "", "lat": None, "lng": None,
        "city": "", "country": "", "first_ts": 0, "last_ts": 0, "count": 0,
    })

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cid = (row.get("checkin_id") or "").strip()
            if not cid:
                continue
            ts  = _int(row.get("date"))
            vid = (row.get("venue_id") or "").strip()
            lat = _float(row.get("lat"))
            lng = _float(row.get("lng"))
            rows.append([
                cid, ts,
                vid or None,
                (row.get("venue") or "").strip() or None,
                (row.get("city") or "").strip() or None,
                (row.get("state") or "").strip() or None,
                (row.get("country") or "").strip() or None,
                (row.get("neighborhood") or "").strip() or None,
                lat, lng,
                (row.get("address") or "").strip() or None,
                (row.get("category") or "").strip() or None,
                (row.get("shout") or "").strip() or None,
                (row.get("with_name") or "").strip() or None,
                (row.get("with_id") or "").strip() or None,
            ])

            if vid:
                m = venue_meta[vid]
                m["count"] += 1
                if ts and (not m["first_ts"] or ts < m["first_ts"]):
                    m["first_ts"] = ts
                if ts and ts > m["last_ts"]:
                    m["last_ts"]   = ts
                    m["name"]     = (row.get("venue") or "").strip()
                    m["category"] = (row.get("category") or "").strip()
                    m["city"]     = (row.get("city") or "").strip()
                    m["country"]  = (row.get("country") or "").strip()
                if lat is not None:
                    m["lat"] = lat
                if lng is not None:
                    m["lng"] = lng

    return rows, dict(venue_meta)


def build_venue_rows(venue_meta: dict) -> list[list]:
    rows = []
    for vid, m in venue_meta.items():
        rows.append([
            vid,
            m["name"] or None,
            m["category"] or None,
            m["lat"], m["lng"],
            m["city"] or None,
            m["country"] or None,
            m["count"],
            m["first_ts"] or None,
            m["last_ts"] or None,
        ])
    return rows


def load_tips(tips_path: str) -> list[list]:
    tips = json.load(open(tips_path, encoding="utf-8"))
    rows = []
    for t in tips:
        rows.append([
            t.get("id"),
            _int(t.get("ts")),
            (t.get("text") or "").strip() or None,
            (t.get("venue") or "").strip() or None,
            (t.get("venue_id") or "").strip() or None,
            (t.get("city") or "").strip() or None,
            (t.get("country") or "").strip() or None,
            _float(t.get("lat")), _float(t.get("lng")),
            (t.get("category") or "").strip() or None,
            _int(t.get("agree_count")),
            _int(t.get("disagree_count")),
            1 if t.get("closed") else 0,
            _int(t.get("view_count")),
        ])
    return rows


def load_ratings(ratings_path: str) -> list[list]:
    data = json.load(open(ratings_path, encoding="utf-8"))
    rows = []
    for rating_key, label in (
        ("venueLikes",    "like"),
        ("venueNeutrals", "neutral"),
        ("venueDislikes", "dislike"),
    ):
        for v in data.get(rating_key) or []:
            vid = (v.get("id") or "").strip()
            if not vid:
                continue
            rows.append([
                vid,
                (v.get("name") or "").strip() or None,
                label,
                _int(v.get("createdAt")),
            ])
    return rows


def load_lists(lists_path: str, visited_vids: set) -> tuple[list[list], list[list]]:
    data = json.load(open(lists_path, encoding="utf-8"))
    raw_lists = data.get("items") or (data if isinstance(data, list) else [])

    list_rows: list[list] = []
    lv_rows:   list[list] = []

    for lst in raw_lists:
        lid = str(lst.get("id") or "").strip()
        if not lid:
            continue

        # cover URL
        ph = lst.get("photo") or {}
        cover = ""
        if ph.get("prefix") and ph.get("suffix"):
            cover = ph["prefix"] + "100x100" + ph["suffix"]

        # updatedAt
        upd_raw = lst.get("updatedAt") or 0
        try:
            upd_ts = int(upd_raw)
        except (ValueError, TypeError):
            upd_ts = 0

        list_rows.append([
            lid,
            (lst.get("name") or "").strip() or None,
            (lst.get("canonicalUrl") or "").strip() or None,
            cover or None,
            upd_ts,
        ])

        items = (lst.get("listItems") or {}).get("items") or []
        for li in items:
            v = li.get("venue") or {}
            vid = str(v.get("id") or "").strip()
            if not vid:
                continue
            loc = v.get("location") or {}
            cats = v.get("categories") or []
            cat = (cats[0].get("name") or "").strip() if cats else ""
            lat = _float(loc.get("lat"))
            lng = _float(loc.get("lng"))
            lv_rows.append([
                lid, vid,
                (v.get("name") or "").strip() or None,
                cat or None,
                lat, lng,
                (loc.get("city") or "").strip() or None,
                (loc.get("country") or "").strip() or None,
                1 if vid in visited_vids else 0,
                0,  # last_visit_ts populated on sync
            ])

    return list_rows, lv_rows


# ── SQL templates ─────────────────────────────────────────────────────────────

SQL_CHECKINS = (
    "INSERT OR REPLACE INTO checkins "
    "(id,date,venue_id,venue,city,state,country,neighborhood,lat,lng,address,category,shout,with_name,with_id) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_VENUES = (
    "INSERT OR REPLACE INTO venues "
    "(id,name,category,lat,lng,city,country,checkin_count,first_checkin_at,last_checkin_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)
SQL_TIPS = (
    "INSERT OR REPLACE INTO tips "
    "(id,ts,text,venue,venue_id,city,country,lat,lng,category,agree_count,disagree_count,closed,view_count) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_RATINGS = (
    "INSERT OR REPLACE INTO ratings "
    "(venue_id,venue_name,rating,created_at) "
    "VALUES (?,?,?,?)"
)
SQL_LISTS = (
    "INSERT OR REPLACE INTO lists (id,name,url,cover,updated_at) VALUES (?,?,?,?,?)"
)
SQL_LIST_VENUES = (
    "INSERT OR REPLACE INTO list_venues "
    "(list_id,venue_id,venue_name,category,lat,lng,city,country,visited,last_visit_ts) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Full one-time D1 import")
    ap.add_argument("--csv",     required=True, help="Path to checkins.csv")
    ap.add_argument("--tips",    required=True, help="Path to tips.json")
    ap.add_argument("--ratings", required=True, help="Path to venueRatings.json")
    ap.add_argument("--lists",   required=True, help="Path to lists.json")
    ap.add_argument("--schema",  default=str(HERE / "d1_schema.sql"))
    ap.add_argument("--skip",    nargs="*", default=[],
                    help="Table names to skip (e.g. checkins venues)")
    ap.add_argument("--token",   help="CF_D1_TOKEN override (else uses env var)")
    args = ap.parse_args()

    token = args.token or os.environ.get("CF_D1_TOKEN", "")
    if not token:
        sys.exit("Set CF_D1_TOKEN env var or pass --token")
    d1.configure(token)
    skip = set(args.skip or [])

    print("── Applying schema …")
    d1.apply_schema(args.schema)

    # ── Load all data ──────────────────────────────────────────────────────────
    print("\n── Loading checkins.csv …")
    checkin_rows, venue_meta = load_checkins(args.csv)
    visited_vids = {r[1] for r in checkin_rows if r[1]}  # venue_id column
    print(f"  {len(checkin_rows):,} check-ins, {len(venue_meta):,} unique venues")

    print("── Loading tips.json …")
    tip_rows = load_tips(args.tips)
    print(f"  {len(tip_rows):,} tips")

    print("── Loading venueRatings.json …")
    rating_rows = load_ratings(args.ratings)
    print(f"  {len(rating_rows):,} ratings")

    print("── Loading lists.json …")
    list_rows, lv_rows = load_lists(args.lists, visited_vids)
    print(f"  {len(list_rows):,} lists, {len(lv_rows):,} list-venue entries")

    total_rows = len(checkin_rows) + len(venue_meta) + len(tip_rows) + len(rating_rows) + len(list_rows) + len(lv_rows)
    print(f"\n  Total rows to write: {total_rows:,}")
    print("  (D1 free tier: 100K writes/day — run with --skip if you hit the limit)\n")

    # ── Insert ─────────────────────────────────────────────────────────────────
    print("── Inserting …")

    if "checkins" not in skip:
        d1.batch_upsert(SQL_CHECKINS, checkin_rows, label="checkins")
    else:
        print("  checkins: skipped")

    if "venues" not in skip:
        venue_rows = build_venue_rows(venue_meta)
        d1.batch_upsert(SQL_VENUES, venue_rows, label="venues  ")
    else:
        print("  venues: skipped")

    if "tips" not in skip:
        d1.batch_upsert(SQL_TIPS, tip_rows, label="tips    ")
    else:
        print("  tips: skipped")

    if "ratings" not in skip:
        d1.batch_upsert(SQL_RATINGS, rating_rows, label="ratings ")
    else:
        print("  ratings: skipped")

    if "lists" not in skip:
        d1.batch_upsert(SQL_LISTS, list_rows, label="lists   ")
    else:
        print("  lists: skipped")

    if "list_venues" not in skip:
        d1.batch_upsert(SQL_LIST_VENUES, lv_rows, label="list_venues")
    else:
        print("  list_venues: skipped")

    print("\n✓  Import complete.")


if __name__ == "__main__":
    main()
