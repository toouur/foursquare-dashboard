# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
import_to_d1.py -- One-time full import of all Foursquare data into Cloudflare D1.

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
import io
from collections import defaultdict
from pathlib import Path

import d1_client as d1

HERE = Path(__file__).parent

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# -- Helpers ------------------------------------------------------------------

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


def _str(v) -> str | None:
    s = (v or "").strip()
    return s or None


# -- Loaders ------------------------------------------------------------------

def load_checkins(csv_path: str) -> tuple[list[list], dict]:
    """
    Returns (rows_for_checkins_table, venue_meta_dict).
    venue_meta keyed by venue_id: {name, category, lat, lng, city, country,
                                   first_ts, last_ts, count}
    All 22 CSV columns are stored.
    """
    rows: list[list] = []
    venue_meta: dict = defaultdict(lambda: {
        "name": "", "category": "", "lat": None, "lng": None,
        "city": "", "country": "", "first_ts": 0, "last_ts": 0, "count": 0,
    })

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cid = _str(row.get("checkin_id"))
            if not cid:
                continue
            ts  = _int(row.get("date"))
            vid = _str(row.get("venue_id"))
            lat = _float(row.get("lat"))
            lng = _float(row.get("lng"))
            rows.append([
                cid,
                ts,
                vid,
                _str(row.get("venue")),
                _str(row.get("venue_url")),
                _str(row.get("city")),
                _str(row.get("state")),
                _str(row.get("country")),
                _str(row.get("neighborhood")),
                lat,
                lng,
                _str(row.get("address")),
                _str(row.get("category")),
                _str(row.get("shout")),
                _str(row.get("source_app")),
                _str(row.get("source_url")),
                _str(row.get("with_name")),
                _str(row.get("with_id")),
                _str(row.get("created_by_name")),
                _str(row.get("created_by_id")),
                _str(row.get("overlaps_name")),
                _str(row.get("overlaps_id")),
            ])

            if vid:
                m = venue_meta[vid]
                m["count"] += 1
                if ts and (not m["first_ts"] or ts < m["first_ts"]):
                    m["first_ts"] = ts
                if ts and ts > m["last_ts"]:
                    m["last_ts"]   = ts
                    m["name"]     = _str(row.get("venue")) or ""
                    m["category"] = _str(row.get("category")) or ""
                    m["city"]     = _str(row.get("city")) or ""
                    m["country"]  = _str(row.get("country")) or ""
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
            _str(t.get("text")),
            _str(t.get("venue")),
            _str(t.get("venue_id")),
            _str(t.get("city")),
            _str(t.get("country")),
            _float(t.get("lat")),
            _float(t.get("lng")),
            _str(t.get("category")),
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
        ("venueOkays",   "okay"),
        ("venueDislikes", "dislike"),
    ):
        for v in data.get(rating_key) or []:
            vid = _str(v.get("id"))
            if vid:
                rows.append([
                    vid,
                    _str(v.get("name")),
                    _str(v.get("url")),
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
        lid = _str(str(lst.get("id") or ""))
        if not lid:
            continue

        ph = lst.get("photo") or {}
        cover = None
        if ph.get("prefix") and ph.get("suffix"):
            cover = ph["prefix"] + "100x100" + ph["suffix"]

        list_rows.append([
            lid,
            _str(lst.get("name")),
            _str(lst.get("canonicalUrl")),
            cover,
            _int(lst.get("updatedAt")),
        ])

        for li in (lst.get("listItems") or {}).get("items") or []:
            v = li.get("venue") or {}
            vid = _str(str(v.get("id") or ""))
            if not vid:
                continue
            loc  = v.get("location") or {}
            cats = v.get("categories") or []
            cat  = cats[0] if cats else {}
            icon = cat.get("icon") or {}

            # formatted_address may be a list or string
            fa_raw = loc.get("formattedAddress")
            if isinstance(fa_raw, list):
                formatted_address = ", ".join(fa_raw)
            else:
                formatted_address = _str(fa_raw)

            lv_rows.append([
                lid,
                vid,
                _int(li.get("createdAt")),
                _str(v.get("name")),
                _str(v.get("canonicalUrl")),
                _str(cat.get("name")),
                _str(cat.get("id")),
                _str(cat.get("shortName")),
                _str(icon.get("prefix")),
                _str(icon.get("suffix")),
                _float(loc.get("lat")),
                _float(loc.get("lng")),
                _str(loc.get("address")),
                _str(loc.get("city")),
                _str(loc.get("state")),
                _str(loc.get("cc")),
                _str(loc.get("country")),
                formatted_address,
                1 if vid in visited_vids else 0,
                0,  # last_visit_ts populated on sync
            ])

    return list_rows, lv_rows


# -- SQL templates ------------------------------------------------------------

SQL_CHECKINS = (
    "INSERT INTO checkins "
    "(id,date,venue_id,venue,venue_url,city,state,country,neighborhood,lat,lng,"
    "address,category,shout,source_app,source_url,with_name,with_id,"
    "created_by_name,created_by_id,overlaps_name,overlaps_id) "
    "VALUES"
)
SQL_VENUES = (
    "INSERT OR REPLACE INTO venues "
    "(id,name,category,lat,lng,city,country,checkin_count,first_checkin_at,last_checkin_at) "
    "VALUES"
)
SQL_TIPS = (
    "INSERT OR REPLACE INTO tips "
    "(id,ts,text,venue,venue_id,city,country,lat,lng,category,"
    "agree_count,disagree_count,closed,view_count) "
    "VALUES"
)
SQL_RATINGS = (
    "INSERT OR REPLACE INTO ratings "
    "(venue_id,venue_name,venue_url,rating,created_at) "
    "VALUES"
)
SQL_LISTS = (
    "INSERT OR REPLACE INTO lists (id,name,url,cover,updated_at) VALUES"
)
SQL_LIST_VENUES = (
    "INSERT OR REPLACE INTO list_venues "
    "(list_id,venue_id,created_at,venue_name,venue_url,category,category_id,"
    "category_short_name,category_icon_prefix,category_icon_suffix,"
    "lat,lng,address,city,state,cc,country,formatted_address,visited,last_visit_ts) "
    "VALUES"
)


# -- Main ---------------------------------------------------------------------

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

    print("-- Applying schema ...")
    d1.apply_schema(args.schema)

    # -- Load all data ---------------------------------------------------------
    print("\n-- Loading checkins.csv ...")
    checkin_rows, venue_meta = load_checkins(args.csv)
    visited_vids = {r[2] for r in checkin_rows if r[2]}  # index 2 = venue_id
    print(f"  {len(checkin_rows):,} check-ins, {len(venue_meta):,} unique venues")

    print("-- Loading tips.json ...")
    tip_rows = load_tips(args.tips)
    print(f"  {len(tip_rows):,} tips")

    print("-- Loading venueRatings.json ...")
    rating_rows = load_ratings(args.ratings)
    print(f"  {len(rating_rows):,} ratings")

    print("-- Loading lists.json ...")
    list_rows, lv_rows = load_lists(args.lists, visited_vids)
    print(f"  {len(list_rows):,} lists, {len(lv_rows):,} list-venue entries")

    total_rows = (len(checkin_rows) + len(venue_meta) + len(tip_rows)
                  + len(rating_rows) + len(list_rows) + len(lv_rows))
    print(f"\n  Total rows to write: {total_rows:,}")
    print("  (D1 free tier: 100K writes/day -- run with --skip if you hit the limit)\n")

    # -- Insert ----------------------------------------------------------------
    print("-- Inserting ...")

    if "checkins" not in skip:
        result = d1.query("SELECT MAX(date) AS max_date FROM checkins")
        max_date = (result[0].get("max_date") or 0) if result else 0
        rows_to_insert = [r for r in checkin_rows if r[1] > max_date]
        print(f"  checkins: {len(rows_to_insert):,} new rows (max_date in D1 = {max_date})")
        d1.raw_upsert(SQL_CHECKINS, rows_to_insert, label="checkins")
    else:
        print("  checkins: skipped")

    if "venues" not in skip:
        venue_rows = build_venue_rows(venue_meta)
        d1.raw_upsert(SQL_VENUES, venue_rows, label="venues  ")
    else:
        print("  venues: skipped")

    if "tips" not in skip:
        d1.raw_upsert(SQL_TIPS, tip_rows, label="tips    ")
    else:
        print("  tips: skipped")

    if "ratings" not in skip:
        d1.raw_upsert(SQL_RATINGS, rating_rows, label="ratings ")
    else:
        print("  ratings: skipped")

    if "lists" not in skip:
        d1.raw_upsert(SQL_LISTS, list_rows, label="lists   ")
    else:
        print("  lists: skipped")

    if "list_venues" not in skip:
        d1.raw_upsert(SQL_LIST_VENUES, lv_rows, label="list_venues")
    else:
        print("  list_venues: skipped")

    print("\nImport complete.")


if __name__ == "__main__":
    main()
