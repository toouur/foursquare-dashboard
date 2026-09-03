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
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import d1_client as d1
import transform as _transform
from metrics import collect_companions

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


# -- Content-hash gate --------------------------------------------------------
# CI drives trips/lists off the *check-ins* fetch flag, so every hour a new
# check-in arrives they re-sync in full even when their own content is
# identical (lists re-sends ~18.5K list_venues rows for nothing). A sha256 of
# the parsed rows, persisted in the sync_state table, lets each table skip the
# D1 write when nothing it cares about actually changed.

def _rows_hash(rows) -> str:
    """Stable sha256 of a row list, independent of dict/key ordering."""
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def _load_sync_hashes() -> dict:
    rows = d1.query("SELECT key, hash FROM sync_state", silent=True)
    return {r["key"]: r["hash"] for r in (rows or [])}


def _save_sync_hash(key: str, h: str) -> None:
    d1.query(
        "INSERT OR REPLACE INTO sync_state (key, hash, updated_at) VALUES (?,?,?)",
        [key, h, int(time.time())],
    )


# -- SQL templates ------------------------------------------------------------

SQL_CHECKINS_NEW = (
    "INSERT INTO checkins "
    "(id,date,venue_id,venue,venue_url,city,state,country,neighborhood,lat,lng,"
    "address,category,shout,source_app,source_url,with_name,with_id,"
    "created_by_name,created_by_id,overlaps_name,overlaps_id,city_inferred) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
SQL_VENUES = (
    "INSERT OR REPLACE INTO venues "
    "(id,name,category,lat,lng,city,country,checkin_count,first_checkin_at,last_checkin_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)
SQL_COMPANIONS = (
    "INSERT INTO companions (name,name_lc,cnt) VALUES (?,?,?)"
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
    "(venue_id,field,old_value,new_value,detected_at,venue_name,action) "
    "VALUES (?,?,?,?,?,?,?)"
)


# -- Loaders ------------------------------------------------------------------

def parse_checkins(csv_path: str, config_dir: str | None = None, city_review: str | None = None,
                   backfill_path: str | None = None):
    rows = []
    venue_meta: dict = defaultdict(lambda: {
        "name": "", "category": "", "lat": None, "lng": None,
        "city": "", "country": "", "first_ts": 0, "last_ts": 0, "count": 0,
    })
    raw_rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig", newline="")))
    # Reconstructed pre-2012 rows (source_app="refurbished", blank venue_id, rf* ids)
    # ride the same normalization + INSERT OR IGNORE path. Their blank venue_id means
    # the `if vid:` guards below skip the venues table + FTS for them automatically.
    if backfill_path and Path(backfill_path).exists():
        bf = list(csv.DictReader(open(backfill_path, encoding="utf-8-sig", newline="")))
        raw_rows.extend(bf)
    if config_dir:
        mappings = _transform.load_mappings(config_dir)
        resolver = None
        if city_review and Path(city_review).exists():
            resolver = _transform.build_blank_city_resolver(city_review)
        raw_rows = _transform.apply_transforms(raw_rows, mappings, blank_city_resolver=resolver)
    for row in raw_rows:
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
            _int(row.get("city_inferred"), 0),
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

def build_companion_rows(rows: list) -> list:
    """Aggregate companion display name -> number of check-ins, from parsed rows.

    Reuses metrics.collect_companions (the same splitter the static pages and
    feed.js collectCompanions use) so a person is named identically everywhere:
    the comma lists are split, the Foursquare "-" overlaps sentinel dropped, and
    duplicates within one check-in counted once. Row indexes follow
    SQL_CHECKINS_NEW: 16=with_name, 18=created_by_name, 20=overlaps_name.

    Precomputing this is what lets /api/search answer a companion query with a
    few-hundred-row scan instead of three full scans of the 70k checkins table.
    """
    counts: dict[str, int] = {}
    casing: dict[str, str] = {}
    for r in rows:
        for name in collect_companions({"with_name": r[16],
                                        "created_by_name": r[18],
                                        "overlaps_name": r[20]}):
            k = name.lower()
            casing.setdefault(k, name)
            counts[k] = counts.get(k, 0) + 1
    return [[casing[k], k, n]
            for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def dedupe_against_d1(candidates: list, source_rows: list, d1_counts: dict) -> list:
    """Keep only as many copies of each check-in id as D1 is still missing.

    checkins carries no UNIQUE on id — seq is the PK, so the CSV's own duplicate
    rows survive the sync, and INSERT OR IGNORE therefore cannot reject a repeat.
    Two syncs that both read "D1 has not got this id yet" before either commits
    will each insert it (update-dashboard runs hourly and recheck-enrich syncs D1
    as well, so their windows overlap). That is how four July 2026 rows ended up
    stored twice.

    Counting, not existence, is what makes this safe: a check-in the source lists
    twice is still inserted twice, while a row D1 already holds is skipped. So
    `WHERE NOT EXISTS` semantics would be wrong here — it would silently collapse
    the twelve legitimately duplicated ids down to one copy each.
    """
    src: dict = {}
    for r in source_rows:
        src[r[0]] = src.get(r[0], 0) + 1
    out, taken = [], {}
    for r in candidates:
        cid = r[0]
        allowed = max(0, src.get(cid, 0) - d1_counts.get(cid, 0))
        if taken.get(cid, 0) < allowed:
            out.append(r)
            taken[cid] = taken.get(cid, 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Incremental D1 sync for CI")
    ap.add_argument("--csv",        default=None)
    ap.add_argument("--backfill",   default=None,
                    help="Optional reconstructed pre-2012 check-in CSV (backfill.csv, "
                         "source_app=refurbished, blank venue_id, rf* ids). Merged into the "
                         "checkins insert stream so the live feed/search return them too.")
    ap.add_argument("--config-dir", dest="config_dir", default=str(HERE.parent / "config"),
                    help="Config dir with city_fixes.json, country_fixes.json, city_merge.yaml "
                         "(default: <repo>/config). Pass empty string to skip transforms.")
    ap.add_argument("--city-review", dest="city_review", default=str(HERE.parent / "city_review.csv"),
                    help="Path to city_review.csv for blank-city resolver (default: <repo>/city_review.csv)")
    ap.add_argument("--tips",    default=None)
    ap.add_argument("--ratings", default=None,
                    help="Path to venueRatings.json (optional; required if --ratings-changed or --force-ratings)")
    ap.add_argument("--lists",   default=None,
                    help="Path to lists.json (optional; required if --lists-changed or --force-lists)")
    ap.add_argument("--trips",   default=None,
                    help="Path to trips_meta.json (written by build.py --trips-out)")
    ap.add_argument("--schema",  default=str(HERE / "d1_schema.sql"))
    ap.add_argument("--schema-only", dest="schema_only", action="store_true",
                    help="Apply the schema (idempotent DDL) and exit -- no data sync, no "
                         "row reads. Lets a deploy guarantee that every table its Pages "
                         "Functions query already exists, even on a run whose full sync is "
                         "gated off (see update-dashboard.yml).")
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
    ap.add_argument("--force-lists",     dest="force_lists",     action="store_true",
                    help="DELETE FROM lists + list_venues then full INSERT OR REPLACE (manual resync)")
    ap.add_argument("--force-checkins", dest="force_checkins", action="store_true",
                    help="DELETE FROM checkins + venues then full reinsert (use after stale-row cleanup)")
    ap.add_argument("--prune-stale-checkins", dest="prune_stale_checkins", action="store_true",
                    help="DELETE checkin rows whose id is absent from the CSV (D1>CSV drift). "
                         "Opt-in only — checkins are append-only by design; guarded by a safety cap "
                         "(--prune-cap) so a truncated CSV fetch can't wipe the table")
    ap.add_argument("--prune-cap", dest="prune_cap", type=int, default=200,
                    help="Abort stale-checkin pruning if it would delete more than this many rows "
                         "(guards against a bad/truncated CSV). Default 200")
    ap.add_argument("--fix-city-country", dest="fix_city_country", action="store_true",
                    help="UPDATE all D1 checkin rows where transform changed city or country "
                         "(fixes blank cities synced before transform pipeline was applied)")
    ap.add_argument("--fix-overlaps-hours", dest="fix_overlaps_hours", type=int, default=0,
                    help="UPDATE overlaps_name/overlaps_id in D1 for check-ins within the last N "
                         "hours whose companions changed since first sync. Check-ins are otherwise "
                         "append-only (INSERT OR IGNORE), so a friend-overlap added to an "
                         "already-synced check-in by the daily recheck never reaches the /api/feed "
                         "without this. Scoped to the recheck window (a few-dozen-row diff).")
    ap.add_argument("--delete-checkin-rows", dest="delete_checkin_rows", default=None,
                    help='JSON file with list of {"venue_id","date"} pairs to DELETE from checkins; '
                         "also prunes orphaned venue_ids from venues table")
    args = ap.parse_args()

    # --csv/--tips are required for every mode EXCEPT --schema-only, which needs
    # no source data at all. Reported through ap.error() so the message and the
    # exit code match argparse's own required-argument handling.
    if not args.schema_only:
        missing = [flag for flag, val in (("--csv", args.csv), ("--tips", args.tips)) if not val]
        if missing:
            ap.error("the following arguments are required: " + ", ".join(missing))

    token = args.token or os.environ.get("CF_D1_TOKEN", "")
    if not token:
        sys.exit("Set CF_D1_TOKEN env var or pass --token")
    d1.configure(token)

    # Schema (idempotent -- CREATE IF NOT EXISTS, no drops)
    print("D1 sync: applying schema ...", flush=True)
    d1.apply_schema(args.schema)

    if args.schema_only:
        print("D1 sync: --schema-only -- schema applied, nothing else to do.", flush=True)
        return

    # Content-hash gate state (sync_state table): lets trips/lists/tips/ratings
    # skip the D1 write when their parsed content is byte-identical to last run.
    sync_hashes = _load_sync_hashes()

    # Daily count census. D1 bills reads as rows SCANNED, so the COUNT(*) pair
    # this script needs (checkins ~70k, venues ~20k) costs ~180k rows read per
    # sync across the five sites that ask for them -- ~1.6M/day against a 5M cap,
    # to re-derive numbers that only move by the handful of rows we write here.
    # Take the real census once per UTC day, cache it in sync_state, and on every
    # other run derive the after-counts from what this run actually inserted.
    # Any opt-in flag whose row delta is unknowable (deletes by id / by
    # (venue_id, date), force resync) forces a fresh census.
    _today_utc = time.strftime("%Y-%m-%d", time.gmtime())
    _cached_counts: dict = {}
    try:
        _cc = json.loads(sync_hashes.get("daily_counts") or "{}")
        if (isinstance(_cc, dict) and _cc.get("date") == _today_utc
                and isinstance(_cc.get("checkins"), int)
                and isinstance(_cc.get("venues"), int)):
            _cached_counts = _cc
    except (ValueError, TypeError):
        _cached_counts = {}
    census = bool(not _cached_counts or args.force_checkins or args.prune_stale_checkins
                  or args.venue_changes or args.delete_checkin_rows)
    print("D1 sync: count census = "
          + ("YES (fresh COUNT(*))" if census else f"no (cached for {_today_utc})"),
          flush=True)

    # Snapshot counts before sync -- used to detect unexpected shrinkage
    _TABLES = ("checkins", "venues", "tips", "ratings", "lists", "list_venues", "trips", "venue_changes")
    counts_before: dict[str, int] = {}
    for tbl in _TABLES:
        # `checkins` is deliberately NOT counted here -- the check-in pass below
        # fills it in. D1 bills reads as rows SCANNED, so COUNT(*) over the 70k-row
        # checkins table costs 70k rows read, and the incremental path already asks
        # for `COUNT(*), MAX(date)` because it needs the watermark anyway. Counting
        # it twice burned ~70k rows/sync (~630k/day) for a duplicate number.
        if tbl == "checkins":
            continue
        # `venues` is the other 20k-row COUNT(*); on a non-census run reuse the
        # cached number instead of re-scanning the table.
        if tbl == "venues" and not census:
            counts_before[tbl] = int(_cached_counts["venues"])
            continue
        try:
            res = d1.query(f"SELECT COUNT(*) AS n FROM {tbl}")
            counts_before[tbl] = res[0].get("n", 0) if res else 0
        except Exception:
            counts_before[tbl] = 0
    print(f"D1 sync: counts before = {counts_before} (checkins counted below)", flush=True)

    # Parse CSV (always needed) — apply full transform pipeline so D1 gets resolved cities/countries
    _cfg = args.config_dir if args.config_dir else None
    _rev = args.city_review if args.config_dir else None
    all_checkin_rows, venue_meta = parse_checkins(args.csv, config_dir=_cfg, city_review=_rev,
                                                   backfill_path=args.backfill)
    visited_vids = {r[2] for r in all_checkin_rows if r[2]}  # index 2 = venue_id

    if args.fix_city_country:
        # Build targeted UPDATEs for all rows where transform changed city or country vs raw CSV.
        # Necessary because existing D1 rows were synced before the transform pipeline ran there.
        raw_rows_by_id = {}
        with open(args.csv, encoding="utf-8-sig", newline="") as _fh:
            for _r in csv.DictReader(_fh):
                cid = _str(_r.get("checkin_id"))
                if cid:
                    raw_rows_by_id[cid] = (_str(_r.get("city")), _str(_r.get("country")), _int(_r.get("city_inferred"), 0))
        stmts = []
        for row in all_checkin_rows:
            cid, _ts, _vid = row[0], row[1], row[2]
            t_city, t_country, t_inferred = row[5], row[7], row[22]
            r_city, r_country, r_inferred = raw_rows_by_id.get(cid, (None, None, 0))
            if t_city != r_city or t_country != r_country or t_inferred != r_inferred:
                city_lit    = d1._sql_val(t_city)
                country_lit = d1._sql_val(t_country)
                id_lit      = d1._sql_val(cid)
                stmts.append(
                    f"UPDATE checkins SET city={city_lit},country={country_lit},"
                    f"city_inferred={t_inferred} WHERE id={id_lit}"
                )
        print(f"  fix-city-country: {len(stmts)} rows to UPDATE", flush=True)
        _CHUNK = 90_000
        chunk: list[str] = []
        chunk_bytes = 0
        sent = 0
        for stmt in stmts:
            sb = len(stmt.encode()) + 2
            if chunk and chunk_bytes + sb > _CHUNK:
                d1._raw_with_retry("; ".join(chunk))
                sent += len(chunk)
                print(f"\r  fix-city-country: {sent}/{len(stmts)}", end="", flush=True)
                chunk = []
                chunk_bytes = 0
            chunk.append(stmt)
            chunk_bytes += sb
        if chunk:
            d1._raw_with_retry("; ".join(chunk))
            sent += len(chunk)
        print(f"\r  fix-city-country: {sent}/{len(stmts)} done    ")

    if args.fix_overlaps_hours > 0:
        # Targeted UPDATE of overlaps_* for rows enriched AFTER their first sync.
        # Check-ins insert as INSERT OR IGNORE (append-only), so a friend-overlap
        # the daily recheck adds to an already-synced check-in never reaches D1 via
        # the normal path — /api/feed would keep showing that check-in without the
        # late companion. Compare the CSV's overlaps_name/id against D1 for rows
        # inside the recheck window and UPDATE any that drifted. Brand-new rows in
        # the window aren't in D1 yet → skipped here, carried by the insert below.
        ov_cutoff = int(time.time()) - args.fix_overlaps_hours * 3600
        # row layout (SQL_CHECKINS_NEW): id=0, date=1, overlaps_name=20, overlaps_id=21
        csv_ov = {row[0]: (row[20], row[21]) for row in all_checkin_rows
                  if row[0] and _int(row[1]) >= ov_cutoff}
        d1_ov: dict = {}
        ov_ids = list(csv_ov.keys())
        for i in range(0, len(ov_ids), 90):  # D1 caps ~100 bindings per statement
            id_chunk = ov_ids[i:i + 90]
            ph = ",".join("?" * len(id_chunk))
            res = d1.query(
                f"SELECT id, overlaps_name, overlaps_id FROM checkins WHERE id IN ({ph})",
                id_chunk)
            for r in (res or []):
                d1_ov[r["id"]] = (r.get("overlaps_name"), r.get("overlaps_id"))
        ov_stmts = []
        for cid, cur in csv_ov.items():
            if cid in d1_ov and cur != d1_ov[cid]:
                ov_stmts.append(
                    f"UPDATE checkins SET overlaps_name={d1._sql_val(cur[0])},"
                    f"overlaps_id={d1._sql_val(cur[1])} WHERE id={d1._sql_val(cid)}"
                )
        print(f"  fix-overlaps: {len(ov_stmts)} row(s) with changed companions to UPDATE",
              flush=True)
        ov_chunk: list[str] = []
        ov_bytes = 0
        ov_sent = 0
        for stmt in ov_stmts:
            sb = len(stmt.encode()) + 2
            if ov_chunk and ov_bytes + sb > 90_000:
                d1._raw_with_retry("; ".join(ov_chunk))
                ov_sent += len(ov_chunk)
                ov_chunk = []
                ov_bytes = 0
            ov_chunk.append(stmt)
            ov_bytes += sb
        if ov_chunk:
            d1._raw_with_retry("; ".join(ov_chunk))
            ov_sent += len(ov_chunk)
        if ov_stmts:
            print(f"  fix-overlaps: {ov_sent}/{len(ov_stmts)} UPDATE(s) applied", flush=True)

    # ── Per-check-in companion overrides (config/companion_fixes.json) ─────────
    # Reconciled on EVERY sync and NOT time-windowed, unlike --fix-overlaps-hours.
    # transform.apply_transforms() has already stamped the corrected names onto
    # all_checkin_rows, but the checkin sync is append-only (INSERT OR IGNORE), so
    # a row already in D1 keeps whatever it was first inserted with — /api/feed
    # would never show the companion. The file holds a handful of ids, so one
    # SELECT + targeted UPDATEs is cheap, idempotent and self-healing (it also
    # repairs a row reset by a force resync or the wrangler bulk-dump path).
    # Deliberately does NOT set `changed`: checkins carry no FTS index.
    cfix_ids: set[str] = set()
    cfix_path = Path(args.config_dir) / "companion_fixes.json" if args.config_dir else None
    if cfix_path and cfix_path.exists():
        try:
            cfix_ids = {k for k in json.load(open(cfix_path, encoding="utf-8"))
                        if not k.startswith("_")}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  companion-fixes: cannot read {cfix_path} — {exc}", flush=True)
    if cfix_ids:
        # Column positions in SQL_CHECKINS_NEW. Deriving the list from
        # transform.COMPANION_FIX_FIELDS means a field added there without a
        # position here fails loudly (KeyError) instead of silently not syncing.
        _CF_POS = {"with_name": 16, "with_id": 17, "overlaps_name": 20, "overlaps_id": 21}
        cf_cols = list(_transform.COMPANION_FIX_FIELDS)
        cf_idx  = [_CF_POS[c] for c in cf_cols]
        csv_cf = {row[0]: tuple(row[i] for i in cf_idx)
                  for row in all_checkin_rows if row[0] in cfix_ids}
        d1_cf: dict = {}
        cf_ids = list(csv_cf.keys())
        for i in range(0, len(cf_ids), 90):   # D1 caps ~100 bindings per statement
            id_chunk = cf_ids[i:i + 90]
            ph = ",".join("?" * len(id_chunk))
            res = d1.query(
                f"SELECT id,{','.join(cf_cols)} FROM checkins WHERE id IN ({ph})", id_chunk)
            for r in (res or []):
                d1_cf[r["id"]] = tuple(r.get(c) for c in cf_cols)
        cf_stmts = []
        for cid, cur in csv_cf.items():
            if cid in d1_cf and cur != d1_cf[cid]:
                sets = ",".join(f"{c}={d1._sql_val(v)}" for c, v in zip(cf_cols, cur))
                cf_stmts.append(f"UPDATE checkins SET {sets} WHERE id={d1._sql_val(cid)}")
        for i in range(0, len(cf_stmts), 50):
            d1._raw_with_retry("; ".join(cf_stmts[i:i + 50]))
        missing = len(cfix_ids) - len(csv_cf)
        print(f"  companion-fixes: {len(cfix_ids)} override(s), {len(cf_stmts)} UPDATE(s) applied"
              + (f", {missing} id(s) not in this CSV" if missing > 0 else ""), flush=True)

    # Per-table FTS dirtiness. `changed` is the coarse CI-facing "something
    # happened" signal (it drives the CHANGED= line workflows read) but it is the
    # WRONG gate for an FTS rebuild: only venues/tips/trips carry an index, and a
    # rebuild reinserts the WHOLE index. Driving all three off `changed` meant one
    # new check-in -- which invalidates no index at all, checkins have none --
    # rebuilt venues_fts (~20k rows), tips_fts and trips_fts on every hourly sync.
    # Each write site below marks only the index it can actually invalidate.
    fts_dirty = {"venues_fts": False, "tips_fts": False, "trips_fts": False}

    # Running row counts, maintained as this sync writes. Only READ on a
    # non-census run -- and a non-census run has, by construction, none of the
    # flags that can delete rows, so "before + what we inserted" is exact.
    checkin_count_after = 0
    venue_count_after = 0

    if args.force_checkins:
        print("  checkins : FORCE full resync — wiping checkins + venues and reinserting", flush=True)
        res = d1.query("SELECT COUNT(*) AS n FROM checkins")
        counts_before["checkins"] = (res[0].get("n") or 0) if res else 0
        d1.query("DELETE FROM checkins")
        d1.query("DELETE FROM venues")
        d1.batch_upsert(SQL_CHECKINS_NEW, all_checkin_rows, label="checkins ")
        all_venue_rows = [
            [vid, m["name"] or None, m["category"] or None, m["lat"], m["lng"],
             m["city"] or None, m["country"] or None, m["count"], m["first_ts"] or None, m["last_ts"] or None]
            for vid, m in venue_meta.items()
        ]
        d1.batch_upsert(SQL_VENUES, all_venue_rows, label="venues   ")
        changed = True
        fts_dirty["venues_fts"] = True
        new_checkin_rows = []   # skip incremental path below
        new_venue_ids: set = set()
        checkin_count_after = len(all_checkin_rows)
        venue_count_after = len(venue_meta)
    else:
        # Get current count + max checkin date from D1
        # MAX(date) alone is an index seek on idx_checkins_date (~1 row read);
        # pairing it with COUNT(*) in one statement forces a full 70k-row scan.
        # The watermark must never be stale, so it is always read live -- only the
        # count comes from the daily census cache.
        if census:
            result = d1.query("SELECT COUNT(*) AS n, MAX(date) AS max_date FROM checkins")
            d1_count = (result[0].get("n") or 0) if result else 0
            max_date = (result[0].get("max_date") or 0) if result else 0
        else:
            result = d1.query("SELECT MAX(date) AS max_date FROM checkins")
            max_date = (result[0].get("max_date") or 0) if result else 0
            d1_count = int(_cached_counts["checkins"])
        counts_before["checkins"] = d1_count   # the pre-sync count, free of a 2nd COUNT(*)
        checkin_count_after = d1_count
        print(f"D1 sync: {d1_count} existing check-ins, last known timestamp = {max_date}",
              flush=True)

        # Reconstructed backfill rows ("rf*" ids) are reconciled by CONTENT, not by
        # id-presence, and are held out of the append-only path below. Their
        # venue/date/city/shout can change behind a STABLE id when backfill.csv is
        # re-edited or renumbered, and append-only id-sync (INSERT OR IGNORE /
        # set-difference) only ever INSERTS a missing id — it never UPDATES an id
        # already in D1 — so an edited backfill row is served stale forever (rf0026
        # kept showing the old venue after the CSV reassigned it to another place).
        # The rf* set is reconciled by delete-and-reinsert further down, but only
        # when its CONTENT actually changed (see the sync_state gate there). The feed reads
        # venue/category straight off the checkins row (no venues join), so
        # refreshing these rows is what fixes /api/feed.
        backfill_rows = [r for r in all_checkin_rows if str(r[0]).startswith("rf")]
        real_rows     = [r for r in all_checkin_rows if not str(r[0]).startswith("rf")]

        # Fast path: real rows strictly newer than D1's newest are always new.
        new_checkin_rows = [r for r in real_rows if r[1] > max_date]

        # The watermark misses backdated / out-of-order real check-ins (ts <=
        # max_date) that D1 has never seen — a manually-added past visit, or a row
        # surfaced by the --recheck-recent-hours sweep. A row-count mismatch forces an
        # authoritative checkin_id set-difference (id present in CSV, absent from D1 =>
        # genuinely new). d1_count includes the rf* rows already in D1, so compare the
        # REAL-row count against D1's real-row count (total minus the rf* subset that
        # the content reconcile below owns) — otherwise the rf* rows skew the guard.
        # Range predicate, not LIKE: SQLite's default case-insensitive LIKE cannot
        # use the BINARY-collated idx_checkins_id, so `id LIKE 'rf%'` was a full
        # 70k-row scan. Real check-in ids are 24-char hex, so nothing real can start
        # with 'r' -- ['rf','rg') is exactly the reconstructed subset.
        rf_d1        = d1.query("SELECT COUNT(*) AS n FROM checkins "
                                "WHERE id >= 'rf' AND id < 'rg'")
        rf_d1_count  = (rf_d1[0].get("n") or 0) if rf_d1 else 0
        real_d1_count = d1_count - rf_d1_count
        if len(real_rows) > real_d1_count + len(new_checkin_rows):
            missing = len(real_rows) - real_d1_count - len(new_checkin_rows)
            print(f"D1 sync: row-count mismatch ({missing} unaccounted) — "
                  f"falling back to checkin_id set-difference", flush=True)
            # Counts, not a set of ids: `r[0] not in existing_ids` drops BOTH
            # copies of an id D1 already holds once, so the twelve check-ins the
            # CSV legitimately lists twice could never finish arriving. Asking
            # for COUNT(*) costs the same one row per id as SELECT id did.
            existing = d1.query("SELECT id, COUNT(*) AS n FROM checkins GROUP BY id")
            all_id_counts = {row["id"]: (row.get("n") or 0) for row in existing} if existing else {}
            new_checkin_rows = dedupe_against_d1(real_rows, real_rows, all_id_counts)

        # Final guard against the double-insert race described in
        # dedupe_against_d1. Only worth it for an incremental batch: on a cold
        # start D1 is empty (nothing to collide with) and a bulk load would cost
        # one IN-query per chunk for no benefit.
        if new_checkin_rows and d1_count and len(new_checkin_rows) <= 1000:
            cand_ids = sorted({r[0] for r in new_checkin_rows})
            d1_counts: dict = {}
            for i in range(0, len(cand_ids), 200):
                chunk = cand_ids[i:i + 200]
                ph = ",".join("?" * len(chunk))
                for row in d1.query(
                        f"SELECT id, COUNT(*) AS n FROM checkins "
                        f"WHERE id IN ({ph}) GROUP BY id", chunk) or []:
                    d1_counts[row["id"]] = row.get("n") or 0
            if d1_counts:
                before = len(new_checkin_rows)
                new_checkin_rows = dedupe_against_d1(new_checkin_rows, real_rows, d1_counts)
                if len(new_checkin_rows) != before:
                    print(f"D1 sync: skipping {before - len(new_checkin_rows)} check-in row(s) "
                          f"already present in D1 (concurrent-sync guard)", flush=True)

        new_venue_ids    = {r[2] for r in new_checkin_rows if r[2]}

        print(f"D1 sync: {len(new_checkin_rows)} new check-ins, "
              f"{len(new_venue_ids)} venues to update", flush=True)

        changed = bool(new_checkin_rows)

        # Upsert checkins (INSERT OR IGNORE -- safe to re-run)
        if new_checkin_rows:
            d1.batch_upsert(SQL_CHECKINS_NEW, new_checkin_rows, label="checkins (new)")
            checkin_count_after += len(new_checkin_rows)

        # Reconcile reconstructed backfill rows by CONTENT: delete the whole rf*
        # subset and reinsert the current CSV's backfill rows, refreshing any that
        # changed behind a reused id. `changed` is intentionally NOT set here —
        # checkins carry no FTS index, so this must not trigger an FTS rebuild.
        #
        # GATED on a sync_state content hash. This block used to run unconditionally
        # every sync, and the rf* set is no longer "a few dozen rows": at ~1.4k rows
        # × (1 + 4 indexes) that is ~7k rows written per delete and ~7k per reinsert,
        # ~14k/sync — over ~9 syncs/day ~125k rows written, i.e. this one block blew
        # the entire 100k/day free-tier write cap on its own. The backfill changes a
        # few times a month, so the hash skips the write on every other run.
        # `rf_d1_count` keeps it self-healing: a hash match with the wrong number of
        # rows actually in D1 (force resync, wrangler bulk dump, partial write) still
        # reconciles.
        if backfill_rows:
            bf_hash = _rows_hash(backfill_rows)
            if bf_hash == sync_hashes.get("backfill") and rf_d1_count == len(backfill_rows):
                print(f"  checkins (backfill): unchanged ({len(backfill_rows)} rf* rows) "
                      f"— skipped", flush=True)
            else:
                print(f"D1 sync: reconciling {len(backfill_rows)} backfill row(s) by "
                      f"content (delete + reinsert rf*)", flush=True)
                d1.query("DELETE FROM checkins WHERE id >= 'rf' AND id < 'rg'")
                d1.batch_upsert(SQL_CHECKINS_NEW, backfill_rows, label="checkins (backfill)")
                checkin_count_after += len(backfill_rows) - rf_d1_count
                _save_sync_hash("backfill", bf_hash)

        # Opt-in: prune checkin rows whose id is no longer in the CSV (D1>CSV
        # drift). Checkins are append-only by design — a bad/truncated CSV fetch
        # must never silently wipe them — so this only runs with the explicit
        # flag and aborts if it would exceed the safety cap.
        if args.prune_stale_checkins:
            csv_ids = {r[0] for r in all_checkin_rows}
            existing = d1.query("SELECT DISTINCT id FROM checkins")
            existing_ids = {row["id"] for row in existing} if existing else set()
            stale_ids = sorted(existing_ids - csv_ids)
            if not stale_ids:
                print("D1 sync: no stale check-ins to prune (D1 ids all present in CSV)", flush=True)
            elif len(stale_ids) > args.prune_cap:
                sys.exit(f"D1 sync: ABORT — {len(stale_ids)} stale check-in ids exceeds "
                         f"--prune-cap={args.prune_cap}. Refusing to delete; the CSV may be "
                         f"truncated. Re-run with a higher cap only if the CSV is known good.")
            else:
                print(f"D1 sync: pruning {len(stale_ids)} stale check-in row(s) absent from CSV",
                      flush=True)
                now_ts = int(time.time())
                audit_rows = []
                for i in range(0, len(stale_ids), 200):
                    chunk = stale_ids[i:i + 200]
                    ph = ",".join("?" * len(chunk))
                    # Capture venue context for the audit trail BEFORE deleting.
                    doomed = d1.query(
                        f"SELECT id, venue_id, venue FROM checkins WHERE id IN ({ph})", chunk)
                    for row in (doomed or []):
                        # Audit subject = checkin id (unique per row → no PK clash);
                        # old_value carries the venue_id it belonged to.
                        audit_rows.append([row["id"], "checkin", row.get("venue_id"),
                                           None, now_ts, row.get("venue"), "deleted"])
                    d1.query(f"DELETE FROM checkins WHERE id IN ({ph})", chunk)
                if audit_rows:
                    d1.batch_upsert(SQL_VENUE_CHANGES, audit_rows,
                                    label="venue_changes(deleted checkins)")
                changed = True

        # Reconcile venues in BOTH directions so the table always equals
        # venue_meta (the CSV aggregation). A pure upsert-of-new-venue-ids path
        # drifts two ways:
        #   D1 < CSV — a venue_id that first appears via a transform / venue_fixes
        #     reassignment, or on a backdated check-in already synced, is never in
        #     new_venue_ids and so was never inserted (missing venues → broken
        #     /api/search & venue-tips lookups).
        #   D1 > CSV — a venue orphaned by a merge / reassignment / archive dedup
        #     lingers (check-in deletion is handled by delete_checkin.py).
        # Query the D1 id set once and drive both add + prune from it.
        # Select the FTS-indexed columns too: D1 bills rows SCANNED, not columns,
        # so widening this SELECT is free — and it lets us notice a venue whose
        # indexed text changed (a rename), which the id-only version could not.
        # The full scan IS the reconcile, and it costs ~20k rows read. Run it on
        # the daily census; on every other sync look up only the venue ids this
        # run actually touched (chunked IN (...) over the PK -- a few hundred rows
        # read), which is enough to upsert them and to spot a rename, but NOT
        # enough to judge orphans or missing-from-D1 (see below).
        meta_ids = set(venue_meta.keys())
        if census:
            existing_venues = d1.query("SELECT id, name, category, city, country FROM venues")
        else:
            existing_venues = []
            probe_ids = sorted(new_venue_ids)
            for i in range(0, len(probe_ids), 200):
                chunk = probe_ids[i:i + 200]
                ph = ",".join("?" * len(chunk))
                existing_venues += d1.query(
                    f"SELECT id, name, category, city, country FROM venues "
                    f"WHERE id IN ({ph})", chunk) or []
        existing_venue_meta = {row["id"]: (row.get("name"), row.get("category"),
                                           row.get("city"), row.get("country"))
                               for row in (existing_venues or [])}
        existing_venue_ids = set(existing_venue_meta)
        # Upsert new-checkin venues plus any CSV venue missing from D1. That second
        # arm needs the FULL D1 id set, so it only runs on a census: with a partial
        # set every untouched venue would read as missing and be re-upserted.
        to_upsert = (new_venue_ids | (meta_ids - existing_venue_ids)) if census else set(new_venue_ids)
        venue_count_after = (len(meta_ids) if census
                             else int(_cached_counts["venues"])
                             + len(new_venue_ids - existing_venue_ids))
        if to_upsert:
            missing_only = ((meta_ids - existing_venue_ids) - new_venue_ids) if census else set()
            if missing_only:
                print(f"D1 sync: {len(missing_only)} venue(s) present in CSV but missing "
                      f"from D1 — inserting", flush=True)
            venue_rows = [
                [vid, m["name"] or None, m["category"] or None, m["lat"], m["lng"],
                 m["city"] or None, m["country"] or None, m["count"], m["first_ts"] or None, m["last_ts"] or None]
                for vid, m in venue_meta.items() if vid in to_upsert
            ]
            d1.batch_upsert(SQL_VENUES, venue_rows, label="venues   ")
            if missing_only:
                changed = True
            # Dirty venues_fts only if an indexed column actually moved: a brand-new
            # venue, or an existing one whose name/category/city/country changed
            # (a venue rename — previously missed entirely, leaving a stale index).
            for r in venue_rows:
                if existing_venue_meta.get(r[0]) != (r[1], r[2], r[5], r[6]):
                    fts_dirty["venues_fts"] = True
                    break
        # Prune orphans absent from the CSV aggregation.
        # Orphan detection needs the complete D1 id set: on a non-census run
        # existing_venue_ids holds only the probed subset, so this difference
        # would read as ~20k orphans and delete the table. Census runs only.
        orphan_ids = sorted(existing_venue_ids - meta_ids) if census else []
        if orphan_ids:
            print(f"D1 sync: removing {len(orphan_ids)} orphaned venue(s) "
                  f"absent from check-in data", flush=True)
            now_ts = int(time.time())
            audit_rows = []
            for i in range(0, len(orphan_ids), 200):
                chunk = orphan_ids[i:i + 200]
                ph = ",".join("?" * len(chunk))
                # Capture names for the audit trail BEFORE deleting.
                doomed = d1.query(f"SELECT id, name FROM venues WHERE id IN ({ph})", chunk)
                for row in (doomed or []):
                    audit_rows.append([row["id"], "venue", row.get("name"),
                                       None, now_ts, row.get("name"), "deleted"])
                d1.query(f"DELETE FROM venues WHERE id IN ({ph})", chunk)
            if audit_rows:
                d1.batch_upsert(SQL_VENUE_CHANGES, audit_rows,
                                label="venue_changes(deleted venues)")
            changed = True
            fts_dirty["venues_fts"] = True

    # Companions (precomputed name -> check-in count, for /api/search)
    companion_rows = build_companion_rows(all_checkin_rows)
    comp_hash = _rows_hash(companion_rows)
    if comp_hash == sync_hashes.get("companions"):
        print(f"  companions: unchanged ({len(companion_rows)} names) - skipped",
              flush=True)
    else:
        print(f"D1 sync: rebuilding companions table ({len(companion_rows)} names)",
              flush=True)
        d1.query("DELETE FROM companions")
        if companion_rows:
            d1.batch_upsert(SQL_COMPANIONS, companion_rows, label="companions")
        _save_sync_hash("companions", comp_hash)
    # No `changed` / fts_dirty: this table has no FTS index and nothing else reads it.

    # Tips
    if args.force_tips:
        print("  tips     : FORCE full resync — wiping and reinserting", flush=True)
        d1.query("DELETE FROM tips")
        tip_rows = parse_tips(args.tips)
        d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
        _save_sync_hash("tips", _rows_hash(tip_rows))
        changed = True
        fts_dirty["tips_fts"] = True
    elif args.tips_changed == "true":
        tip_rows = parse_tips(args.tips)
        h = _rows_hash(tip_rows)
        if h == sync_hashes.get("tips"):
            print("  tips     : skipped (content hash unchanged)", flush=True)
        else:
            d1.batch_upsert(SQL_TIPS, tip_rows, label="tips     ")
            _save_sync_hash("tips", h)
            changed = True
            fts_dirty["tips_fts"] = True
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
        _save_sync_hash("ratings", _rows_hash(rating_rows))
        changed = True
    elif args.ratings_changed == "true":
        if not args.ratings:
            sys.exit("--ratings-changed=true requires --ratings")
        rating_rows = parse_ratings(args.ratings)
        h = _rows_hash(rating_rows)
        if h == sync_hashes.get("ratings"):
            print("  ratings  : skipped (content hash unchanged)", flush=True)
        else:
            d1.batch_upsert(SQL_RATINGS, rating_rows, label="ratings  ")
            _save_sync_hash("ratings", h)
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
        _save_sync_hash("trips", _rows_hash(trip_rows))
        changed = True
        fts_dirty["trips_fts"] = True
    elif args.trips_changed == "true" and args.trips:
        if Path(args.trips).exists():
            trip_rows = parse_trips(args.trips)
            h = _rows_hash(trip_rows)
            if h == sync_hashes.get("trips"):
                print("  trips    : skipped (content hash unchanged)", flush=True)
            else:
                d1.batch_upsert(SQL_TRIPS, trip_rows, label="trips    ")
                _save_sync_hash("trips", h)
                changed = True
                fts_dirty["trips_fts"] = True
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
        # Separate merge records (field='venue_id') from metadata field updates
        merge_diffs = [r for r in diffs if r.get("field") == "venue_id" and r.get("venue_id") and r.get("new_value")]

        if by_venue or merge_diffs:
            print(f"  venue_changes: applying {len(diffs)} diff(s) across {len(by_venue)} venue(s)"
                  + (f", {len(merge_diffs)} merge(s)" if merge_diffs else ""), flush=True)
            # Field mappings: checkins and tips share the same column names for venue metadata
            # venues table uses 'name' instead of 'venue' for the venue name
            VENUE_TABLE_FIELD = {"venue": "name", "city": "city", "country": "country",
                                 "lat": "lat", "lng": "lng", "category": "category"}
            # Build all UPDATE statements as raw SQL and batch via /raw endpoint.
            # Prior approach: 3 individual d1.query() calls per venue → ~23K HTTP round-trips
            # for large snapshot diffs. Raw batching collapses this to ~10-50 requests.
            raw_stmts: list[str] = []
            for vid, recs in by_venue.items():
                vid_lit = d1._sql_val(vid)
                set_checkins = ", ".join(
                    f"{r['field']}={d1._sql_val(r['new_value'])}" for r in recs
                )
                raw_stmts.append(f"UPDATE checkins SET {set_checkins} WHERE venue_id={vid_lit}")
                raw_stmts.append(f"UPDATE tips SET {set_checkins} WHERE venue_id={vid_lit}")
                set_venues = ", ".join(
                    f"{VENUE_TABLE_FIELD[r['field']]}={d1._sql_val(r['new_value'])}" for r in recs
                )
                raw_stmts.append(f"UPDATE venues SET {set_venues} WHERE id={vid_lit}")
            # Venue merges: reassign checkins + tips to the new venue_id AND copy
            # the destination venue's denormalized display columns onto the moved
            # rows. Reassigning venue_id alone left the feed — which reads venue /
            # category / city / country / lat / lng straight off each checkins row,
            # not via a join — still showing the old venue's name, so a merged
            # venue rendered as a separate card.
            merge_dest_vids: set = set()
            for r in merge_diffs:
                old_lit = d1._sql_val(r["venue_id"])
                new_vid = r["new_value"]
                new_lit = d1._sql_val(new_vid)
                nm = venue_meta.get(new_vid)
                if nm:
                    merge_dest_vids.add(new_vid)
                    # Denormalized columns shared by checkins + tips. Skip lat/lng
                    # when the destination lacks coords so we never NULL good data.
                    cols = [
                        ("venue", nm["name"]), ("category", nm["category"]),
                        ("city", nm["city"]), ("country", nm["country"]),
                    ]
                    if nm["lat"] is not None:
                        cols.append(("lat", nm["lat"]))
                    if nm["lng"] is not None:
                        cols.append(("lng", nm["lng"]))
                    set_meta = ", ".join(f"{c}={d1._sql_val(v)}" for c, v in cols)
                    raw_stmts.append(
                        f"UPDATE checkins SET {set_meta}, venue_id={new_lit} WHERE venue_id={old_lit}")
                    raw_stmts.append(
                        f"UPDATE tips SET {set_meta}, venue_id={new_lit} WHERE venue_id={old_lit}")
                else:
                    # Destination venue absent from the current CSV aggregation —
                    # fall back to a bare venue_id reassignment (old behaviour).
                    raw_stmts.append(f"UPDATE checkins SET venue_id={new_lit} WHERE venue_id={old_lit}")
                    raw_stmts.append(f"UPDATE tips SET venue_id={new_lit} WHERE venue_id={old_lit}")
            # Send in ~90 KB chunks via /raw
            _CHUNK = 90_000
            chunk = []
            chunk_bytes = 0
            total_stmts = len(raw_stmts)
            sent_stmts = 0
            for stmt in raw_stmts:
                sb = len(stmt.encode()) + 2  # "; " separator
                if chunk and chunk_bytes + sb > _CHUNK:
                    d1._raw_with_retry("; ".join(chunk))
                    sent_stmts += len(chunk)
                    print(f"\r  venue_updates: {sent_stmts}/{total_stmts}", end="", flush=True)
                    chunk = []
                    chunk_bytes = 0
                chunk.append(stmt)
                chunk_bytes += sb
            if chunk:
                d1._raw_with_retry("; ".join(chunk))
                sent_stmts += len(chunk)
            print(f"\r  venue_updates: {total_stmts}/{total_stmts} done    ")
            # Refresh merge-destination venue rows so venues.checkin_count reflects
            # the post-merge total. The main venues reconciliation skips them — the
            # id already exists in D1 and none of its check-ins are "new" — so
            # /api/search would otherwise keep showing the pre-merge count. The
            # orphaned source venue_id is pruned by that same reconciliation pass.
            if merge_dest_vids:
                dest_rows = [
                    [vid, venue_meta[vid]["name"] or None, venue_meta[vid]["category"] or None,
                     venue_meta[vid]["lat"], venue_meta[vid]["lng"],
                     venue_meta[vid]["city"] or None, venue_meta[vid]["country"] or None,
                     venue_meta[vid]["count"], venue_meta[vid]["first_ts"] or None,
                     venue_meta[vid]["last_ts"] or None]
                    for vid in merge_dest_vids
                ]
                d1.batch_upsert(SQL_VENUES, dest_rows, label="venues(merge)")
            # Audit log
            def _derive_action(field: str) -> str:
                if field == "venue":
                    return "renamed"
                if field in ("lat", "lng", "city", "country", "address"):
                    return "relocated"
                if field == "category":
                    return "recategorized"
                return "updated"

            vc_rows = [
                [
                    r["venue_id"], r["field"], r.get("old_value"), r.get("new_value"),
                    r.get("detected_at", 0),
                    venue_meta.get(r["venue_id"], {}).get("name", ""),
                    _derive_action(r["field"]),
                ]
                for r in diffs if r.get("venue_id") and r.get("field") in ALLOWED_FIELDS
            ]
            # Merge audit rows — use venue_name from the diff record (old vid not in venue_meta)
            vc_rows += [
                [
                    r["venue_id"], "venue_id", r.get("old_value"), r.get("new_value"),
                    r.get("detected_at", 0),
                    r.get("venue_name", ""),
                    "merged",
                ]
                for r in merge_diffs
            ]
            d1.batch_upsert(SQL_VENUE_CHANGES, vc_rows, label="venue_changes")
            changed = True
            # A venue diff rewrites venues.name/category/city/country AND the
            # denormalized tips.venue/city/country — both are FTS-indexed columns.
            fts_dirty["venues_fts"] = True
            fts_dirty["tips_fts"] = True
        else:
            print("  venue_changes: no valid diffs found", flush=True)
    elif args.venue_changes:
        print(f"  venue_changes: file not found: {args.venue_changes}", flush=True)

    # Targeted checkin row deletion + orphaned venue pruning
    if args.delete_checkin_rows and os.path.exists(args.delete_checkin_rows):
        pairs = json.load(open(args.delete_checkin_rows, encoding="utf-8"))
        deleted_checkins = 0
        now_ts = int(time.time())
        vc_merge_rows = []
        for p in pairs:
            vid  = str(p["venue_id"])
            date = int(p["date"])
            vname = str(p.get("venue_name", ""))
            d1.query("DELETE FROM checkins WHERE venue_id=? AND date=?", [vid, date])
            deleted_checkins += 1
            vc_merge_rows.append([vid, "venue_id", vid, None, now_ts, vname, "merged"])
        print(f"  checkins : deleted {deleted_checkins} stale row(s) by (venue_id, date)", flush=True)
        if vc_merge_rows:
            d1.batch_upsert(SQL_VENUE_CHANGES, vc_merge_rows, label="venue_changes(merged)")
        # Prune venue_ids from venues table that no longer appear in checkins
        stale_vids = [str(p["venue_id"]) for p in pairs]
        pruned = 0
        for vid in stale_vids:
            res = d1.query("SELECT COUNT(*) AS n FROM checkins WHERE venue_id=?", [vid])
            remaining = (res[0].get("n", 0) if res else 0)
            if remaining == 0:
                d1.query("DELETE FROM venues WHERE id=?", [vid])
                pruned += 1
                print(f"    pruned venue {vid} (no remaining check-ins)", flush=True)
        print(f"  venues   : pruned {pruned} orphaned venue(s)", flush=True)
        changed = True
        if pruned:
            fts_dirty["venues_fts"] = True
    elif args.delete_checkin_rows:
        print(f"  delete_checkin_rows: file not found: {args.delete_checkin_rows}", flush=True)

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
        _save_sync_hash("lists", _rows_hash([list_rows, lv_rows]))
        changed = True
    elif args.lists_changed == "true":
        if not args.lists:
            sys.exit("--lists-changed=true requires --lists")
        list_rows, lv_rows = parse_lists(args.lists, visited_vids)
        h = _rows_hash([list_rows, lv_rows])
        if h == sync_hashes.get("lists"):
            print("  lists    : skipped (content hash unchanged)", flush=True)
        else:
            _sync_lists_diff(list_rows, lv_rows)
            _save_sync_hash("lists", h)
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
    counts_after: dict[str, int] = {}
    # The two big tables come from the running accumulators on a non-census run
    # (~90k rows read saved); the small ones are cheap to count either way.
    _derived_after = {"checkins": checkin_count_after, "venues": venue_count_after}
    for tbl in _TABLES:
        if not census and tbl in _derived_after:
            after = _derived_after[tbl]
        else:
            try:
                res = d1.query(f"SELECT COUNT(*) AS n FROM {tbl}")
                after = res[0].get("n", 0) if res else 0
            except Exception:
                after = counts_before.get(tbl, 0)
        counts_after[tbl] = after
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

    # Source-vs-D1 drift check. The shrinkage guard above only compares D1
    # against ITS OWN previous count, so a table that is persistently wrong
    # relative to the source files (e.g. append-only checkins accumulating
    # phantom rows, or venues missing an out-of-band id) never trips it. Compare
    # the final D1 counts against the source aggregation and warn on any gap.
    source_expected = {"checkins": len(all_checkin_rows), "venues": len(venue_meta)}
    for tbl, expected in source_expected.items():
        # Reuse the count taken moments ago rather than re-scanning: COUNT(*) over
        # checkins+venues is ~90k rows read, and nothing writes in between.
        after = counts_after.get(tbl)
        if after is None:
            continue
        if after != expected:
            drift = after - expected
            hint = ("run --prune-stale-checkins to reconcile" if tbl == "checkins" and drift > 0
                    else "venues auto-reconcile each run; investigate if this persists" if tbl == "venues"
                    else "")
            msg = (f"D1 DRIFT: {tbl} D1={after} vs source={expected} "
                   f"({'+' if drift > 0 else ''}{drift}){' — ' + hint if hint else ''}")
            alerts.append(msg)
            print(f"::warning::{msg}" if in_gha else f"WARNING: {msg}", flush=True)

    if not alerts:
        print("D1 sync: all counts stable or growing", flush=True)

    # Persist the day's counts so the rest of this UTC day can skip the COUNT(*)s.
    # A stale-LOW checkins count trips the mismatch guard above and falls back to
    # the exact GROUP BY id scan (correct, merely expensive); a stale-HIGH one only
    # means that fallback is not attempted, and the live MAX(date) watermark still
    # carries every genuinely new row. Both self-correct at the next census.
    _cc_ck = counts_after.get("checkins")
    _cc_vn = counts_after.get("venues")
    if isinstance(_cc_ck, int) and isinstance(_cc_vn, int) and _cc_ck > 0 and _cc_vn > 0:
        _save_sync_hash("daily_counts", json.dumps(
            {"date": _today_utc, "checkins": _cc_ck, "venues": _cc_vn}))

    # ── Rebuild FTS5 search indexes ──────────────────────────────────────────
    # venues_fts / tips_fts / trips_fts are external-content FTS5 tables (see
    # d1_schema.sql). The canonical repopulate is the `('rebuild')` special
    # command, but it must NOT be trusted blindly on D1: if it ever silently
    # no-ops — or an index was left empty by the wrangler bulk-resync path, which
    # never touches FTS — search returns nothing forever and nothing notices.
    # So we rebuild, then VERIFY the index row count against the base table and,
    # if the rebuild didn't take, fall back to an explicit `delete-all` +
    # `INSERT..SELECT` repopulate (a plain write that always lands on D1). The
    # empty-index check runs every sync, so a stranded index self-heals on the
    # very next run whether or not this run changed data.
    FTS_TABLES = (
        ("venues_fts", "venues", ("name", "category", "city", "country")),
        ("tips_fts",   "tips",   ("venue", "text", "city", "country")),
        ("trips_fts",  "trips",  ("name", "countries", "cities")),
    )

    def _count(table: str) -> int:
        try:
            res = d1.query(f"SELECT COUNT(*) AS n FROM {table}")
            return int((res or [{}])[0].get("n", 0))
        except Exception:
            return -1   # unknown (treated as "may be empty")

    def _warn(msg: str) -> None:
        print(f"::warning::{msg}" if in_gha else f"WARNING: {msg}", flush=True)

    def _is_empty(table: str) -> bool:
        """Cheap emptiness probe: ~1 row read, where COUNT(*) scans the whole index.

        The self-heal only needs a yes/no here, but it asks on EVERY sync for all
        three indexes — as COUNT(*) that was ~20k rows read per run (~200k/day)
        spent on a question a single row answers.
        """
        try:
            res = d1.query(f"SELECT rowid FROM {table} LIMIT 1")
        except Exception:
            return True    # unknown → treat as "may be empty" and rebuild
        return not res

    for fts_table, base_table, cols in FTS_TABLES:
        # `changed` is true for ANY write this sync — new check-ins included — but
        # checkins has NO FTS table, so the hourly "3 new check-ins" run used to
        # rebuild all three indexes for nothing. Rebuild only an index whose own
        # base table was written this run (or one that is actually empty).
        if not (fts_dirty[fts_table] or _is_empty(fts_table)):
            continue
        try:
            d1.query(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
        except Exception as exc:
            # A missing FTS module or DDL race must not fail the whole sync —
            # the explicit repopulate below (or the next run) recovers.
            _warn(f"FTS rebuild for {fts_table} failed: {exc}")

        # Verify the rebuild actually populated the index; if not, hard-repopulate.
        base_n = _count(base_table)
        fts_n  = _count(fts_table)
        if fts_n <= 0 and base_n > 0:
            col_list = ", ".join(cols)
            try:
                try:
                    d1.query(f"INSERT INTO {fts_table}({fts_table}) VALUES('delete-all')")
                except Exception:
                    pass   # empty/contentless already — nothing to clear
                d1.query(
                    f"INSERT INTO {fts_table}(rowid, {col_list}) "
                    f"SELECT rowid, {col_list} FROM {base_table}"
                )
                fts_n = _count(fts_table)
                print(f"  {fts_table}: repopulated via INSERT..SELECT ({fts_n} rows)", flush=True)
            except Exception as exc:
                _warn(f"FTS repopulate for {fts_table} failed: {exc}")
        else:
            print(f"  {fts_table}: rebuilt from {base_table} ({fts_n} rows)", flush=True)

    print(f"CHANGED={'true' if changed else 'false'}", flush=True)
    print("D1 sync: done", flush=True)


if __name__ == "__main__":
    main()