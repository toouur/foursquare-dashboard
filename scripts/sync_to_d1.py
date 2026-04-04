# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
sync_to_d1.py — Incremental CI sync: upserts only changed data to D1.

Strategy:
  checkins   → INSERT OR IGNORE (append-only; never overwrites existing rows)
  venues     → INSERT OR REPLACE only for venues touched by new check-ins
  tips       → INSERT OR REPLACE all (~1.9K rows — counts change over time)
  ratings    → INSERT OR REPLACE all (~3.7K rows — new likes added each run)
  lists      → INSERT OR REPLACE all + rebuild list_venues (~small)

Outputs CHANGED=true/false to stdout (captured by GitHub Actions).

Usage (CI):
    python scripts/sync_to_d1.py \
        --csv    private-data/checkins.csv \
        --tips   private-data/tips.json \
        --ratings private-data/venueRatings.json \
        --lists  private-data/lists.json \
        --schema scripts/d1_schema.sql
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


# ── Shared helpers (same as import_to_d1) ────────────────────────────────────

def _float(v):
    try:
        return float(v) if v not in (None, "", "0", 0) else None
    except (ValueError, TypeError):
        return None


def _int(v, default=0):
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


# ── SQL templates ─────────────────────────────────────────────────────────────

SQL_CHECKINS_IGNORE = (
    "INSERT OR IGNORE INTO checkins "
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


# ── Loaders (identical logic to import_to_d1, kept local for independence) ───

def parse_checkins(csv_path: str):
    rows = []
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


def parse_tips(tips_path: str):
    tips = json.load(open(tips_path, encoding="utf-8"))
    return [[
        t.get("id"), _int(t.get("ts")),
        (t.get("text") or "").strip() or None,
        (t.get("venue") or "").strip() or None,
        (t.get("venue_id") or "").strip() or None,
        (t.get("city") or "").strip() or None,
        (t.get("country") or "").strip() or None,
        _float(t.get("lat")), _float(t.get("lng")),
        (t.get("category") or "").strip() or None,
        _int(t.get("agree_count")), _int(t.get("disagree_count")),
        1 if t.get("closed") else 0, _int(t.get("view_count")),
    ] for t in tips]


def parse_ratings(ratings_path: str):
    data = json.load(open(ratings_path, encoding="utf-8"))
    rows = []
    for key, label in (("venueLikes", "like"), ("venueNeutrals", "neutral"), ("venueDislikes", "dislike")):
        for v in data.get(key) or []:
            vid = (v.get("id") or "").strip()
            if vid:
                rows.append([vid, (v.get("name") or "").strip() or None, label, _int(v.get("createdAt"))])
    return rows


def parse_lists(lists_path: str, visited_vids: set):
    data = json.load(open(lists_path, encoding="utf-8"))
    raw = data.get("items") or (data if isinstance(data, list) else [])
    list_rows, lv_rows = [], []
    for lst in raw:
        lid = str(lst.get("id") or "").strip()
        if not lid:
            continue
        ph = lst.get("photo") or {}
        cover = (ph.get("prefix", "") + "100x100" + ph.get("suffix", "")) if ph.get("prefix") and ph.get("suffix") else None
        upd_ts = _int(lst.get("updatedAt"))
        list_rows.append([lid, (lst.get("name") or "").strip() or None,
                           (lst.get("canonicalUrl") or "").strip() or None, cover, upd_ts])
        for li in (lst.get("listItems") or {}).get("items") or []:
            v = li.get("venue") or {}
            vid = str(v.get("id") or "").strip()
            if not vid:
                continue
            loc = v.get("location") or {}
            cats = v.get("categories") or []
            cat = (cats[0].get("name") or "").strip() if cats else None
            lv_rows.append([
                lid, vid, (v.get("name") or "").strip() or None, cat,
                _float(loc.get("lat")), _float(loc.get("lng")),
                (loc.get("city") or "").strip() or None,
                (loc.get("country") or "").strip() or None,
                1 if vid in visited_vids else 0, 0,
            ])
    return list_rows, lv_rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Incremental D1 sync for CI")
    ap.add_argument("--csv",     required=True)
    ap.add_argument("--tips",    required=True)
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--lists",   required=True)
    ap.add_argument("--schema",  default=str(HERE / "d1_schema.sql"))
    ap.add_argument("--token",   help="CF_D1_TOKEN override")
    args = ap.parse_args()

    token = args.token or os.environ.get("CF_D1_TOKEN", "")
    if not token:
        sys.exit("Set CF_D1_TOKEN env var or pass --token")
    d1.configure(token)

    # ── Schema (idempotent) ────────────────────────────────────────────────────
    print("D1 sync: applying schema ...", flush=True)
    d1.apply_schema(args.schema)

    # ── Get current max checkin date from D1 ──────────────────────────────────
    result = d1.query("SELECT MAX(date) AS max_date FROM checkins")
    max_date = (result[0].get("max_date") or 0) if result else 0
    print(f"D1 sync: last known checkin timestamp = {max_date}", flush=True)

    # ── Parse CSV ─────────────────────────────────────────────────────────────
    all_checkin_rows, venue_meta = parse_checkins(args.csv)
    visited_vids = {r[2] for r in all_checkin_rows if r[2]}  # index 2 = venue_id

    # Only rows newer than what D1 already has
    new_checkin_rows = [r for r in all_checkin_rows if r[1] > max_date]
    new_venue_ids    = {r[2] for r in new_checkin_rows if r[2]}

    print(f"D1 sync: {len(new_checkin_rows)} new check-ins, "
          f"{len(new_venue_ids)} venues to update", flush=True)

    changed = bool(new_checkin_rows)

    # ── Upsert checkins (INSERT OR IGNORE — safe to re-run) ───────────────────
    if new_checkin_rows:
        d1.batch_upsert(SQL_CHECKINS_IGNORE, new_checkin_rows, label="checkins (new)")

    # ── Upsert only affected venues ───────────────────────────────────────────
    if new_venue_ids:
        venue_rows = [
            [vid, m["name"] or None, m["category"] or None, m["lat"], m["lng"],
             m["city"] or None, m["country"] or None, m["count"], m["first_ts"] or None, m["last_ts"] or None]
            for vid, m in venue_meta.items() if vid in new_venue_ids
        ]
        d1.batch_upsert(SQL_VENUES, venue_rows, label="venues   ")

    # ── Tips — full upsert (counts change, new tips added) ───────────────────
    tip_rows = parse_tips(args.tips)
    d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
    changed = changed or bool(tip_rows)

    # ── Ratings — full upsert ─────────────────────────────────────────────────
    rating_rows = parse_ratings(args.ratings)
    d1.batch_upsert(SQL_RATINGS, rating_rows, label="ratings  ")

    # ── Lists — full upsert ───────────────────────────────────────────────────
    list_rows, lv_rows = parse_lists(args.lists, visited_vids)
    d1.batch_upsert(SQL_LISTS,       list_rows, label="lists    ")
    d1.batch_upsert(SQL_LIST_VENUES, lv_rows,   label="list_venues")

    print(f"CHANGED={'true' if changed else 'false'}", flush=True)
    print("D1 sync: done", flush=True)


if __name__ == "__main__":
    main()
