# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
sync_to_d1.py -- Incremental CI sync: upserts only changed data to D1.

Strategy:
  checkins   -> INSERT OR IGNORE (append-only; never overwrites existing rows)
  venues     -> INSERT OR REPLACE only for venues touched by new check-ins
  tips       -> INSERT OR REPLACE all (~1.9K rows -- counts change over time)
               skipped when --tips-changed=false (CI passes fetch_tips output)
  ratings    -> INSERT OR REPLACE all (~3.7K rows)
               skipped when --ratings-changed=false (CI passes fetch_ratings output)
  lists      -> INSERT OR REPLACE all + rebuild list_venues (~18K rows)
               skipped when --lists-changed=false (CI passes fetch/checkins output)
  ratings    -> INSERT OR REPLACE all (~3.7K rows -- new likes added each run)
  lists      -> INSERT OR REPLACE all + rebuild list_venues (~small)

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


# -- Helpers ------------------------------------------------------------------

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


def _str(v) -> str | None:
    s = (v or "").strip()
    return s or None


# -- SQL templates ------------------------------------------------------------

SQL_CHECKINS_NEW = (
    "INSERT INTO checkins "
    "(id,date,venue_id,venue,venue_url,city,state,country,neighborhood,lat,lng,"
    "address,category,shout,source_app,source_url,with_name,with_id,"
    "created_by_name,created_by_id,overlaps_name,overlaps_id) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_VENUES = (
    "INSERT OR REPLACE INTO venues "
    "(id,name,category,lat,lng,city,country,checkin_count,first_checkin_at,last_checkin_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)
SQL_TIPS = (
    "INSERT OR REPLACE INTO tips "
    "(id,ts,text,venue,venue_id,city,country,lat,lng,category,"
    "agree_count,disagree_count,closed,view_count) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_RATINGS = (
    "INSERT OR REPLACE INTO ratings "
    "(venue_id,venue_name,venue_url,rating,created_at) "
    "VALUES (?,?,?,?,?)"
)
SQL_LISTS = (
    "INSERT OR REPLACE INTO lists (id,name,url,cover,updated_at) VALUES (?,?,?,?,?)"
)
SQL_LIST_VENUES = (
    "INSERT OR REPLACE INTO list_venues "
    "(list_id,venue_id,created_at,venue_name,venue_url,category,category_id,"
    "category_short_name,category_icon_prefix,category_icon_suffix,"
    "lat,lng,address,city,state,cc,country,formatted_address,visited,last_visit_ts) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_TRIPS = (
    "INSERT OR REPLACE INTO trips "
    "(id,name,start_date,end_date,start_ts,start_year,duration,"
    "checkin_count,unique_places,countries,cities,tags,top_cats) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_VENUE_CHANGES = (
    "INSERT OR REPLACE INTO venue_changes "
    "(venue_id,field,old_value,new_value,detected_at) "
    "VALUES (?,?,?,?,?)"
)


# -- Loaders ------------------------------------------------------------------

def parse_checkins(csv_path: str):
    rows = []
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


def parse_tips(tips_path: str):
    tips = json.load(open(tips_path, encoding="utf-8"))
    return [[
        t.get("id"), _int(t.get("ts")),
        _str(t.get("text")),
        _str(t.get("venue")),
        _str(t.get("venue_id")),
        _str(t.get("city")),
        _str(t.get("country")),
        _float(t.get("lat")), _float(t.get("lng")),
        _str(t.get("category")),
        _int(t.get("agree_count")), _int(t.get("disagree_count")),
        1 if t.get("closed") else 0, _int(t.get("view_count")),
    ] for t in tips]


def parse_ratings(ratings_path: str):
    data = json.load(open(ratings_path, encoding="utf-8"))
    rows = []
    for key, label in (("venueLikes", "like"), ("venueOkays", "okay"), ("venueDislikes", "dislike")):
        for v in data.get(key) or []:
            vid = _str(v.get("id"))
            if vid:
                rows.append([vid, _str(v.get("name")), _str(v.get("url")), label, _int(v.get("createdAt"))])
    return rows


def parse_trips(trips_path: str):
    data = json.load(open(trips_path, encoding="utf-8"))
    rows = []
    for t in data:
        rows.append([
            _int(t.get("id")),
            _str(t.get("name")),
            _str(t.get("start_date")),
            _str(t.get("end_date")),
            _int(t.get("start_ts")),
            _int(t.get("start_year")),
            _int(t.get("duration")),
            _int(t.get("checkin_count")),
            _int(t.get("unique_places")),
            json.dumps(t.get("countries") or [], ensure_ascii=False),
            json.dumps(t.get("cities") or [], ensure_ascii=False),
            json.dumps(t.get("tags") or [], ensure_ascii=False),
            json.dumps(t.get("top_cats") or [], ensure_ascii=False),
        ])
    return rows


def parse_lists(lists_path: str, visited_vids: set):
    data = json.load(open(lists_path, encoding="utf-8"))
    raw = data.get("items") or (data if isinstance(data, list) else [])
    list_rows, lv_rows = [], []
    for lst in raw:
        lid = _str(str(lst.get("id") or ""))
        if not lid:
            continue
        ph = lst.get("photo") or {}
        cover = (ph.get("prefix", "") + "100x100" + ph.get("suffix", "")) if ph.get("prefix") and ph.get("suffix") else None
        list_rows.append([lid, _str(lst.get("name")),
                          _str(lst.get("canonicalUrl")), cover, _int(lst.get("updatedAt"))])
        for li in (lst.get("listItems") or {}).get("items") or []:
            v = li.get("venue") or {}
            vid = _str(str(v.get("id") or ""))
            if not vid:
                continue
            loc  = v.get("location") or {}
            cats = v.get("categories") or []
            cat  = cats[0] if cats else {}
            icon = cat.get("icon") or {}
            fa_raw = loc.get("formattedAddress")
            if isinstance(fa_raw, list):
                formatted_address = ", ".join(fa_raw)
            else:
                formatted_address = _str(fa_raw)
            lv_rows.append([
                lid, vid,
                _int(li.get("createdAt")),
                _str(v.get("name")),
                _str(v.get("canonicalUrl")),
                _str(cat.get("name")),
                _str(cat.get("id")),
                _str(cat.get("shortName")),
                _str(icon.get("prefix")),
                _str(icon.get("suffix")),
                _float(loc.get("lat")), _float(loc.get("lng")),
                _str(loc.get("address")),
                _str(loc.get("city")),
                _str(loc.get("state")),
                _str(loc.get("cc")),
                _str(loc.get("country")),
                formatted_address,
                1 if vid in visited_vids else 0,
                0,
            ])
    return list_rows, lv_rows


# -- Main ---------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Incremental D1 sync for CI")
    ap.add_argument("--csv",     required=True)
    ap.add_argument("--tips",    required=True)
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--lists",   required=True)
    ap.add_argument("--trips",   default=None,
                    help="Path to trips_meta.json (written by build.py --trips-out)")
    ap.add_argument("--schema",  default=str(HERE / "d1_schema.sql"))
    ap.add_argument("--token",   help="CF_D1_TOKEN override")
    ap.add_argument("--tips-changed",    dest="tips_changed",
                    default="false", choices=("true", "false"),
                    help="Sync tips when 'true' (pass fetch_tips CHANGED output; default false)")
    ap.add_argument("--ratings-changed", dest="ratings_changed",
                    default="false", choices=("true", "false"),
                    help="Sync ratings when 'true' (pass fetch_ratings CHANGED output; default false)")
    ap.add_argument("--lists-changed",   dest="lists_changed",
                    default="false", choices=("true", "false"),
                    help="Sync lists/list_venues when 'true' (pass fetch CHANGED output; default false)")
    ap.add_argument("--trips-changed",   dest="trips_changed",
                    default="false", choices=("true", "false"),
                    help="Sync trips when 'true' (pass fetch CHANGED output; default false)")
    ap.add_argument("--venue-changes", dest="venue_changes", default=None,
                    help="Path to venue diffs JSON from sync_venue_changes.py --out; "
                         "applies targeted UPDATE checkins SET field WHERE venue_id + "
                         "inserts audit rows into venue_changes table")
    args = ap.parse_args()

    token = args.token or os.environ.get("CF_D1_TOKEN", "")
    if not token:
        sys.exit("Set CF_D1_TOKEN env var or pass --token")
    d1.configure(token)

    # Schema (idempotent -- CREATE IF NOT EXISTS, no drops)
    print("D1 sync: applying schema ...", flush=True)
    d1.apply_schema(args.schema)

    # Snapshot counts before sync -- used to detect unexpected shrinkage
    _TABLES = ("checkins", "venues", "tips", "ratings", "lists", "list_venues", "trips", "venue_changes")
    counts_before: dict[str, int] = {}
    for tbl in _TABLES:
        try:
            res = d1.query(f"SELECT COUNT(*) AS n FROM {tbl}")
            counts_before[tbl] = res[0].get("n", 0) if res else 0
        except Exception:
            counts_before[tbl] = 0
    print(f"D1 sync: counts before = {counts_before}", flush=True)

    # Get current max checkin date from D1
    result = d1.query("SELECT MAX(date) AS max_date FROM checkins")
    max_date = (result[0].get("max_date") or 0) if result else 0
    print(f"D1 sync: last known checkin timestamp = {max_date}", flush=True)

    # Parse CSV
    all_checkin_rows, venue_meta = parse_checkins(args.csv)
    visited_vids = {r[2] for r in all_checkin_rows if r[2]}  # index 2 = venue_id

    # Only rows newer than what D1 already has
    new_checkin_rows = [r for r in all_checkin_rows if r[1] > max_date]
    new_venue_ids    = {r[2] for r in new_checkin_rows if r[2]}

    print(f"D1 sync: {len(new_checkin_rows)} new check-ins, "
          f"{len(new_venue_ids)} venues to update", flush=True)

    changed = bool(new_checkin_rows)

    # Upsert checkins (INSERT OR IGNORE -- safe to re-run)
    if new_checkin_rows:
        d1.batch_upsert(SQL_CHECKINS_NEW, new_checkin_rows, label="checkins (new)")

    # Upsert only affected venues
    if new_venue_ids:
        venue_rows = [
            [vid, m["name"] or None, m["category"] or None, m["lat"], m["lng"],
             m["city"] or None, m["country"] or None, m["count"], m["first_ts"] or None, m["last_ts"] or None]
            for vid, m in venue_meta.items() if vid in new_venue_ids
        ]
        d1.batch_upsert(SQL_VENUES, venue_rows, label="venues   ")

    # Tips -- full upsert only when tips file changed this run
    if args.tips_changed == "true":
        tip_rows = parse_tips(args.tips)
        d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
        changed = True
    else:
        print("  tips     : skipped (no new tips this run)", flush=True)

    # Ratings -- full upsert only when ratings file changed this run
    if args.ratings_changed == "true":
        rating_rows = parse_ratings(args.ratings)
        d1.batch_upsert(SQL_RATINGS, rating_rows, label="ratings  ")
        changed = True
    else:
        print("  ratings  : skipped (no new ratings this run)", flush=True)

    # Trips -- full upsert only when checkins changed (trip detection uses checkins)
    if args.trips_changed == "true" and args.trips:
        if Path(args.trips).exists():
            trip_rows = parse_trips(args.trips)
            d1.batch_upsert(SQL_TRIPS, trip_rows, label="trips    ")
            changed = True
        else:
            print(f"  trips    : file not found: {args.trips}", flush=True)
    else:
        print("  trips    : skipped (no new check-ins this run)", flush=True)

    # Venue changes -- targeted UPDATE of checkins rows + audit log
    if args.venue_changes and Path(args.venue_changes).exists():
        diffs = json.load(open(args.venue_changes, encoding="utf-8"))
        # Only these fields are safe to UPDATE from a venue diff
        ALLOWED_FIELDS = {"venue", "city", "country", "lat", "lng", "category"}
        # Group diffs by venue_id
        by_venue: dict[str, list] = {}
        for rec in diffs:
            vid = rec.get("venue_id")
            field = rec.get("field")
            if vid and field in ALLOWED_FIELDS:
                by_venue.setdefault(vid, []).append(rec)
        if by_venue:
            print(f"  venue_changes: applying {len(diffs)} diff(s) across {len(by_venue)} venue(s)", flush=True)
            # Field mappings: checkins and tips share the same column names for venue metadata
            # venues table uses 'name' instead of 'venue' for the venue name
            VENUE_TABLE_FIELD = {"venue": "name", "city": "city", "country": "country",
                                 "lat": "lat", "lng": "lng", "category": "category"}
            for vid, recs in by_venue.items():
                set_clauses = ", ".join(f"{r['field']}=?" for r in recs)
                set_vals = [r["new_value"] for r in recs]
                # Update all checkins rows for this venue
                d1.query(f"UPDATE checkins SET {set_clauses} WHERE venue_id=?", set_vals + [vid])
                # Update tips rows for this venue (same column names)
                d1.query(f"UPDATE tips SET {set_clauses} WHERE venue_id=?", set_vals + [vid])
                # Update venues table row (column 'name' instead of 'venue')
                v_clauses = ", ".join(f"{VENUE_TABLE_FIELD[r['field']]}=?" for r in recs)
                d1.query(f"UPDATE venues SET {v_clauses} WHERE id=?", set_vals + [vid])
            # Audit log
            vc_rows = [
                [r["venue_id"], r["field"], r.get("old_value"), r.get("new_value"), r.get("detected_at", 0)]
                for r in diffs if r.get("venue_id") and r.get("field") in ALLOWED_FIELDS
            ]
            d1.batch_upsert(SQL_VENUE_CHANGES, vc_rows, label="venue_changes")
            changed = True
        else:
            print("  venue_changes: no valid diffs found", flush=True)
    elif args.venue_changes:
        print(f"  venue_changes: file not found: {args.venue_changes}", flush=True)

    # Lists -- full upsert only when checkins changed (visited status) this run
    if args.lists_changed == "true":
        list_rows, lv_rows = parse_lists(args.lists, visited_vids)
        d1.batch_upsert(SQL_LISTS,       list_rows, label="lists    ")
        d1.batch_upsert(SQL_LIST_VENUES, lv_rows,   label="list_venues")
        changed = True
    else:
        print("  lists    : skipped (no new check-ins this run)", flush=True)

    # Post-sync count check -- alert if any table shrank
    in_gha = os.environ.get("GITHUB_ACTIONS") == "true"
    alerts: list[str] = []
    for tbl in _TABLES:
        try:
            res = d1.query(f"SELECT COUNT(*) AS n FROM {tbl}")
            after = res[0].get("n", 0) if res else 0
        except Exception:
            after = counts_before.get(tbl, 0)
        before = counts_before.get(tbl, 0)
        delta = after - before
        status = f"+{delta}" if delta >= 0 else str(delta)
        print(f"  {tbl}: {before} -> {after} ({status})", flush=True)
        if after < before:
            msg = f"D1 ALERT: {tbl} shrank from {before} to {after} (lost {before - after} rows) -- review immediately"
            alerts.append(msg)
            if in_gha:
                print(f"::warning::{msg}", flush=True)
            else:
                print(f"WARNING: {msg}", flush=True)

    if not alerts:
        print("D1 sync: all counts stable or growing", flush=True)

    print(f"CHANGED={'true' if changed else 'false'}", flush=True)
    print("D1 sync: done", flush=True)


if __name__ == "__main__":
    main()
