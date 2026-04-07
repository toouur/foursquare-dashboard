# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
sync_to_d1.py -- Incremental CI sync: upserts only changed data to D1.

Strategy (CI):
  checkins   -> INSERT OR IGNORE (append-only; never overwrites existing rows)
  venues     -> INSERT OR REPLACE only for venues touched by new check-ins
  tips       -> INSERT OR REPLACE all (~1.9K rows -- counts change over time)
               skipped when --tips-changed=false
  ratings    -> INSERT OR IGNORE (append-only; likes only on CI, no deletions)
               skipped when --ratings-changed=false
  trips      -> INSERT OR REPLACE (counts update when new check-in joins old trip)
               skipped when --trips-changed=false
  lists      -> smart diff: add/delete/update only changed rows
               skipped when --lists-changed=false

Force-resync flags (manual / post-export):
  --force-ratings   DELETE FROM ratings; full INSERT OR REPLACE from JSON
  --force-tips      DELETE FROM tips;    full INSERT OR REPLACE from JSON
  --force-trips     DELETE FROM trips;   full INSERT OR REPLACE from JSON
  --force-lists     DELETE FROM lists + list_venues; full INSERT OR REPLACE

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
SQL_RATINGS_IGNORE = (
    "INSERT OR IGNORE INTO ratings "
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


# -- Helpers ------------------------------------------------------------------

def _sync_lists_diff(list_rows: list, lv_rows: list) -> None:
    """
    True incremental sync for lists and list_venues.
    - Adds new lists
    - Deletes removed lists
    - For each list, inserts new venues, deletes removed venues,
      and updates visited status for existing venues.
    """
    CHUNK_SIZE = 90

    # ---- 1. Sync lists table (handles new & deleted lists) ----
    new_list_ids = {row[0] for row in list_rows}
    existing_res = d1.query("SELECT id FROM lists")
    existing_list_ids = {row["id"] for row in existing_res} if existing_res else set()

    # Delete lists that no longer exist
    to_delete_ids = existing_list_ids - new_list_ids
    if to_delete_ids:
        to_delete_list = list(to_delete_ids)
        for i in range(0, len(to_delete_list), CHUNK_SIZE):
            chunk = to_delete_list[i:i+CHUNK_SIZE]
            ph = ",".join("?" * len(chunk))
            d1.query(f"DELETE FROM list_venues WHERE list_id IN ({ph})", chunk)
            d1.query(f"DELETE FROM lists WHERE id IN ({ph})", chunk)
        print(f"  lists    : deleted {len(to_delete_ids)} removed list(s)")

    # Insert or replace all lists (handles new & renamed)
    d1.batch_upsert(SQL_LISTS, list_rows, label="lists    ")

    # ---- 2. Build current D1 state for list_venues ----
    # Fetch all existing list_venues as a dict: (list_id, venue_id) -> (visited, last_visit_ts)
    existing_lv = d1.query("SELECT list_id, venue_id, visited, last_visit_ts FROM list_venues")
    existing_map = {}
    for row in existing_lv:
        key = (row["list_id"], row["venue_id"])
        existing_map[key] = (row.get("visited", 0), row.get("last_visit_ts", 0))

    # Build new data as dict: (list_id, venue_id) -> full row (list of values)
    new_map = {}
    for row in lv_rows:
        key = (row[0], row[1])  # list_id, venue_id
        new_map[key] = row

    # ---- 3. Compute diffs ----
    to_insert = []
    to_delete = []
    to_update_visited = []  # rows that only need visited flag update

    # Find venues that are in new data but not in existing
    for key, new_row in new_map.items():
        if key not in existing_map:
            to_insert.append(new_row)

    # Find venues that are in existing but not in new
    for key in existing_map:
        if key not in new_map:
            to_delete.append(key)

    # Find venues that exist in both but have changed visited flag or last_visit_ts
    for key, new_row in new_map.items():
        if key in existing_map:
            old_visited, old_last_ts = existing_map[key]
            # visited is at index 18, last_visit_ts at index 19 (0-based)
            new_visited = new_row[18] if len(new_row) > 18 else 0
            new_last_ts = new_row[19] if len(new_row) > 19 else 0
            if old_visited != new_visited or old_last_ts != new_last_ts:
                to_update_visited.append((key[0], key[1], new_visited, new_last_ts))

    # ---- 4. Apply changes ----
    # Insert new rows (use raw_upsert for speed)
    if to_insert:
        base_sql = "INSERT INTO list_venues (" \
                   "list_id,venue_id,created_at,venue_name,venue_url,category,category_id," \
                   "category_short_name,category_icon_prefix,category_icon_suffix," \
                   "lat,lng,address,city,state,cc,country,formatted_address,visited,last_visit_ts" \
                   ") VALUES"
        d1.raw_upsert(base_sql, to_insert, label="list_venues (insert)")
        print(f"  list_venues: inserted {len(to_insert)} new venue(s)")

    # Delete removed rows
    if to_delete:
        # Group by list_id for efficient deletion
        del_by_list = {}
        for list_id, venue_id in to_delete:
            del_by_list.setdefault(list_id, []).append(venue_id)
        for list_id, venue_ids in del_by_list.items():
            for i in range(0, len(venue_ids), CHUNK_SIZE):
                chunk = venue_ids[i:i+CHUNK_SIZE]
                ph = ",".join("?" * len(chunk))
                d1.query(f"DELETE FROM list_venues WHERE list_id = ? AND venue_id IN ({ph})", [list_id] + chunk)
        print(f"  list_venues: deleted {len(to_delete)} removed venue(s)")

    # Update visited status (single column update is cheap)
    if to_update_visited:
        for list_id, venue_id, visited, last_ts in to_update_visited:
            d1.query(
                "UPDATE list_venues SET visited = ?, last_visit_ts = ? WHERE list_id = ? AND venue_id = ?",
                [visited, last_ts, list_id, venue_id]
            )
        print(f"  list_venues: updated visited for {len(to_update_visited)} venue(s)")


# -- Main ---------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Incremental D1 sync for CI")
    ap.add_argument("--csv",     required=True)
    ap.add_argument("--tips",    required=True)
    ap.add_argument("--ratings", default=None,
                    help="Path to venueRatings.json (optional; required if --ratings-changed or --force-ratings)")
    ap.add_argument("--lists",   default=None,
                    help="Path to lists.json (optional; required if --lists-changed or --force-lists)")
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
    # Force-resync flags: bypass change gates, DELETE table, then full INSERT OR REPLACE
    ap.add_argument("--force-ratings", dest="force_ratings", action="store_true",
                    help="DELETE FROM ratings then full INSERT OR REPLACE (manual resync)")
    ap.add_argument("--force-tips",    dest="force_tips",    action="store_true",
                    help="DELETE FROM tips then full INSERT OR REPLACE (manual resync)")
    ap.add_argument("--force-trips",   dest="force_trips",   action="store_true",
                    help="DELETE FROM trips then full INSERT OR REPLACE (manual resync)")
    ap.add_argument("--force-lists",   dest="force_lists",   action="store_true",
                    help="DELETE FROM lists + list_venues then full INSERT OR REPLACE (manual resync)")
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

    # Tips
    if args.force_tips:
        print("  tips     : FORCE full resync — wiping and reinserting", flush=True)
        d1.query("DELETE FROM tips")
        tip_rows = parse_tips(args.tips)
        d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
        changed = True
    elif args.tips_changed == "true":
        tip_rows = parse_tips(args.tips)
        d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
        changed = True
    else:
        print("  tips     : skipped (no new tips this run)", flush=True)

    # Ratings
    # CI path: INSERT OR IGNORE (append-only; likes only, deletions handled by --force-ratings)
    # Force path: DELETE + full INSERT OR REPLACE (use after data export comparison)
    if args.force_ratings:
        if not args.ratings:
            sys.exit("--force-ratings requires --ratings")
        print("  ratings  : FORCE full resync — wiping and reinserting", flush=True)
        d1.query("DELETE FROM ratings")
        rating_rows = parse_ratings(args.ratings)
        d1.batch_upsert(SQL_RATINGS, rating_rows, label="ratings  ")
        changed = True
    elif args.ratings_changed == "true":
        if not args.ratings:
            sys.exit("--ratings-changed=true requires --ratings")
        rating_rows = parse_ratings(args.ratings)
        d1.batch_upsert(SQL_RATINGS_IGNORE, rating_rows, label="ratings  ")
        changed = True
    else:
        print("  ratings  : skipped (no new ratings this run)", flush=True)

    # Trips
    if args.force_trips:
        if not args.trips or not Path(args.trips).exists():
            sys.exit(f"--force-trips requires --trips pointing to an existing file (got: {args.trips!r})")
        print("  trips    : FORCE full resync — wiping and reinserting", flush=True)
        d1.query("DELETE FROM trips")
        trip_rows = parse_trips(args.trips)
        d1.batch_upsert(SQL_TRIPS, trip_rows, label="trips    ")
        changed = True
    elif args.trips_changed == "true" and args.trips:
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

    # Lists
    # Force path: full wipe + reinsert (manual, post-export)
    # CI path: smart diff — delete removed lists/items, upsert current state
    if args.force_lists:
        if not args.lists:
            sys.exit("--force-lists requires --lists")
        print("  lists    : FORCE full resync — wiping and reinserting", flush=True)
        d1.query("DELETE FROM list_venues")
        d1.query("DELETE FROM lists")
        list_rows, lv_rows = parse_lists(args.lists, visited_vids)
        d1.batch_upsert(SQL_LISTS,       list_rows, label="lists    ")
        d1.batch_upsert(SQL_LIST_VENUES, lv_rows,   label="list_venues")
        changed = True
    elif args.lists_changed == "true":
        if not args.lists:
            sys.exit("--lists-changed=true requires --lists")
        list_rows, lv_rows = parse_lists(args.lists, visited_vids)
        _sync_lists_diff(list_rows, lv_rows)
        changed = True
    else:
        print("  lists    : skipped (no new check-ins this run)", flush=True)

    # Post-sync count check -- alert if any table shrank unexpectedly
    # (force-resync tables may legitimately shrink; that's intentional)
    force_resynced = set()
    if args.force_tips:    force_resynced.add("tips")
    if args.force_ratings: force_resynced.add("ratings")
    if args.force_trips:   force_resynced.add("trips")
    if args.force_lists:   force_resynced.update(("lists", "list_venues"))

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
            if tbl in force_resynced:
                print(f"  (shrinkage expected — force resync removed {before - after} rows)", flush=True)
            else:
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