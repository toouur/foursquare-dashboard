# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
build.py  –  CLI entry point. Reads checkins.csv → index.html + trips.html
Run:  python scripts/build.py [--input data/checkins.csv] [--config-dir config]
             [--home-city Minsk] [--min-checkins 5] [--output-dir .]
"""
import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from transform import load_mappings, apply_transforms, build_blank_city_resolver
from metrics import process

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# Directory that contains this script (scripts/) and the project root (one level up)
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent


def load_settings(config_dir: Path) -> dict:
    path = config_dir / "settings.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_category_list(rows: list[dict], out_path: str) -> None:
    from collections import Counter
    cats = Counter(r.get("category", "") for r in rows if r.get("category", "").strip())
    lines = ["FULL CATEGORY LIST", "=" * 60,
             f"Total unique categories: {len(cats)}", ""]
    for cat, n in cats.most_common():
        lines.append(f"  {n:6,}  {cat}")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log.info("Category list → %s  (%d categories)", out_path, len(cats))


def generate_feed_meta(rows: list[dict], output_dir: str):
    """Create feed_meta.json with ym_index, total, first_ts.
    Replaces expensive D1 query for month index.
    """
    if not rows:
        log.warning("No rows to generate feed_meta.json")
        return

    # Sort from newest to oldest (same as API order)
    rows_sorted = sorted(rows, key=lambda x: int(x.get("date", 0) or 0), reverse=True)
    total = len(rows_sorted)
    first_ts = int(rows_sorted[-1]["date"]) if total > 0 else 0

    ym_index = {}
    for idx, row in enumerate(rows_sorted):
        ts = int(row.get("date", 0) or 0)
        if ts == 0:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc)
        ym = d.strftime("%Y-%m")
        if ym not in ym_index:
            ym_index[ym] = idx   # first (newest) check-in in that month

    meta = {
        "ym_index": ym_index,
        "total": total,
        "first_ts": first_ts,
    }
    out_path = os.path.join(output_dir, "feed_meta.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    log.info("Generated %s (months: %d, total: %d)", out_path, len(ym_index), total)


# Templates are loaded at import time from the templates/ directory.
# Edit templates/index.html and templates/trips.html directly;
# they are proper HTML files, visible to linters and formatters.
_TEMPLATES_DIR = _PROJECT_ROOT / "templates"
TEMPLATE       = (_TEMPLATES_DIR / "index.html.tmpl").read_text(encoding="utf-8")
TRIPS_TEMPLATE = (_TEMPLATES_DIR / "trips.html.tmpl").read_text(encoding="utf-8")

def build(data, trips, out_dir='.', extra_replacements=None, pix_dir_json='""'):
    import os
    # ── index.html ──────────────────────────────────────────────────────────
    html = TEMPLATE
    html = html.replace('{{DATE_MIN}}',  data['date_min'])
    html = html.replace('{{DATE_MAX}}',  data['date_max'])
    html = html.replace('{{TOTAL}}',     f"{data['total']:,}")
    html = html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    html = html.replace('{{CITIES}}',    f"{len(data['cities']):,}")
    html = html.replace('{{PLACES}}',    f"{data['unique_places_count']:,}")
    html = html.replace('{{UPDATED}}',   datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    html = html.replace('{{TRIPS}}',      str(data['trips_count']))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    if extra_replacements:
        for key, val in extra_replacements.items():
            html = html.replace(key, val)
    idx_path = os.path.join(out_dir, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f: f.write(html)
    print(f"Built ->{idx_path}  ({len(html)//1024:,} KB)")

    # ── trips.html ──────────────────────────────────────────────────────────
    trips_html = TRIPS_TEMPLATE
    trips_html = trips_html.replace('{{TRIPS_JSON}}', json.dumps(trips, ensure_ascii=False).replace('</', '<\\/'))
    trips_html = trips_html.replace('{{TOTAL_TRIPS}}', str(len(trips)))
    trips_html = trips_html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    trips_html = trips_html.replace('{{PIX_DIR_JSON}}', pix_dir_json)
    trips_html = trips_html.replace('{{STATS}}', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    if extra_replacements:
        for key, val in extra_replacements.items():
            trips_html = trips_html.replace(key, val)
    trips_path = os.path.join(out_dir, 'trips.html')
    with open(trips_path, 'w', encoding='utf-8') as f: f.write(trips_html)
    print(f"Built ->{trips_path}  ({len(trips_html)//1024:,} KB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Foursquare check-in dashboard")
    parser.add_argument("--input",       default=str(_PROJECT_ROOT / "data" / "checkins.csv"),
                        help="Input CSV file (default: data/checkins.csv)")
    parser.add_argument("--config-dir",  default=str(_PROJECT_ROOT / "config"),
                        help="Directory with config JSON/YAML files (default: config/)")
    parser.add_argument("--output-dir",  default=str(_PROJECT_ROOT),
                        help="Output directory for HTML files (default: project root)")
    parser.add_argument("--home-city",   default=None,
                        help="Override home city (default: from settings.yaml, fallback Minsk)")
    parser.add_argument("--min-checkins",type=int, default=None,
                        help="Override min check-ins for a trip")
    parser.add_argument("--cat-list",    action="store_true",
                        help="Also write category_list.txt")
    parser.add_argument("--photos",      default=None,
                        help="Path to photos.json (checkin_id → [filenames]); "
                             "also infers pix/ dir as sibling. When supplied, "
                             "trip-{id}.html pages are generated in output-dir.")
    parser.add_argument("--pix-url",     default=None,
                        help="Base URL for photos (e.g. https://pub-xxx.r2.dev). "
                             "Overrides local pix/ dir resolution.")
    parser.add_argument("--ratings",     default=None,
                        help="Path to venueRatings.json (Foursquare export). "
                             "Falls back to venueRatings.json sibling of --input.")
    parser.add_argument("--lists",       default=None,
                        help="Path to lists.json (Foursquare export). "
                             "Falls back to lists.json sibling of --input.")
    parser.add_argument("--trips-out",   default="trips_meta.json",
                        help="Write slim trips metadata JSON to this path "
                             "(used by sync_to_d1.py; excludes checkins/coords arrays). "
                             "Defaults to trips_meta.json in the current directory.")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        log.error("Input file not found: %s", args.input)
        sys.exit(1)

    config_dir = Path(args.config_dir)
    settings   = load_settings(config_dir)
    trip_cfg   = settings.get("trip_detection", {})

    home_city     = args.home_city     or trip_cfg.get("home_city",    "Minsk")
    min_checkins  = args.min_checkins  or trip_cfg.get("min_checkins", 5)
    fs_user_id    = settings.get("dashboard", {}).get("foursquare_user_id", "")

    log.info("Loading mappings from %s …", config_dir)
    mappings = load_mappings(config_dir)

    log.info("Reading %s …", args.input)
    with open(args.input, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    log.info("  %d rows loaded", len(rows))

    # Blank-city inference: if the review CSV exists next to the config dir,
    # resolve blank city fields using timestamp + coordinate matching.
    review_csv = config_dir / "city_merge_normalized_review.csv"
    blank_resolver = build_blank_city_resolver(review_csv)

    rows = apply_transforms(rows, mappings, blank_city_resolver=blank_resolver)

    # ── Generate static feed_meta.json (replaces expensive D1 query) ─────────
    generate_feed_meta(rows, args.output_dir)

    # ── Generate venues_filter.json (year + catgrp heatmap layers, loaded lazily) ─
    # Extracted from S to keep index.html lean; fetched on first map filter use.
    def _write_venues_filter(data_dict: dict, out_dir: str) -> None:
        payload = {
            "by_year":    data_dict.get("venues_by_year", {}),
            "by_catgrp":  data_dict.get("venues_by_catgrp", {}),
        }
        out_path = os.path.join(out_dir, "venues_filter.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        log.info("venues_filter.json → %s  (%d KB)",
                 out_path, os.path.getsize(out_path) // 1024)

    trip_names_path = config_dir / "trip_names.json"
    trip_names: dict = {}
    if trip_names_path.exists():
        with open(trip_names_path, encoding="utf-8") as fh:
            trip_names = json.load(fh)
        log.info("Loaded %d trip name override(s) from %s", len(trip_names), trip_names_path)

    trip_exclude_path = config_dir / "trip_exclude.json"
    trip_exclude: set[int] = set()
    if trip_exclude_path.exists():
        with open(trip_exclude_path, encoding="utf-8") as fh:
            trip_exclude = set(json.load(fh))
        log.info("Loaded %d trip exclusion(s) from %s", len(trip_exclude), trip_exclude_path)

    trip_end_overrides_path = config_dir / "trip_end_overrides.json"
    trip_end_overrides: dict[int, int] = {}
    if trip_end_overrides_path.exists():
        with open(trip_end_overrides_path, encoding="utf-8") as fh:
            trip_end_overrides = {int(k): v for k, v in json.load(fh).items()}
        log.info("Loaded %d trip end override(s) from %s", len(trip_end_overrides), trip_end_overrides_path)

    trip_start_overrides_path = config_dir / "trip_start_overrides.json"
    trip_start_overrides: dict[int, int] = {}
    if trip_start_overrides_path.exists():
        with open(trip_start_overrides_path, encoding="utf-8") as fh:
            trip_start_overrides = {int(k): v for k, v in json.load(fh).items()}
        log.info("Loaded %d trip start override(s) from %s", len(trip_start_overrides), trip_start_overrides_path)

    trip_tags_path = config_dir / "trip_tags.json"
    trip_tags: dict[int, list[str]] = {}
    if trip_tags_path.exists():
        with open(trip_tags_path, encoding="utf-8") as fh:
            trip_tags = {int(k): v for k, v in json.load(fh).items()}
        log.info("Loaded %d trip tag(s) from %s", len(trip_tags), trip_tags_path)

    nc_yr_overrides: dict[str, int] = {
        str(k): int(v)
        for k, v in settings.get("new_country_year_overrides", {}).items()
    }

    log.info("Computing metrics (home=%s, min_checkins=%d) …", home_city, min_checkins)
    data, trips = process(rows, mappings, home_city=home_city, min_trip_checkins=min_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags, new_country_year_overrides=nc_yr_overrides)

    # ── Flights: parse FR24 flight-diary CSV if present ─────────────────────
    # data/flights.csv lives next to checkins.csv in the private data repo.
    flights_path = Path(args.input).resolve().parent / "flights.csv"
    flight_matches: dict[str, dict] = {}
    if flights_path.exists():
        try:
            from flights import (
                load_flights, build_iata_coords, match_to_checkins, summarise as _flights_summary,
            )
            _fl = load_flights(flights_path)
            _iata = build_iata_coords(rows, _fl)
            flight_matches = match_to_checkins(_fl, rows, _iata)
            data["flight_history"] = _flights_summary(_fl, _iata)
            data["flight_matches"] = flight_matches    # checkin_id → flight info
            log.info("Flights: %d parsed | %d IATA airports resolved | %d check-ins matched",
                     len(_fl), len(_iata), len(flight_matches))
        except Exception as _fe:
            log.warning("flights.csv load failed: %s", _fe)
    else:
        log.info("No flights.csv at %s — Flight History card will be hidden", flights_path)

    # Enrich the metrics.recent[] list with matched flight info so the index
    # recent strip can show "✈ PC842 MSQ→IST · A320" inline.
    if flight_matches:
        for r in data.get("recent", []):
            cid = r.get("checkin_id", "")
            if cid in flight_matches:
                r["flight"] = flight_matches[cid]

    # Build a recent-flights list for the index "Recent Flights" cards strip.
    # Re-parse flights here (cheap — ~127 rows) so build.py owns the recent
    # subset shape; the full history is already on the stats page.
    data["flights_recent"] = []
    if flights_path.exists():
        try:
            from flights import load_flights as _load_flights
            _all_fl = _load_flights(flights_path)
            data["flights_recent"] = list(reversed(_all_fl))[:15]   # newest first
        except Exception:
            pass

    # Write per-year / per-catgrp heatmap layers as a separate static file.
    # Remove from data dict so they don't bloat index.html.
    _write_venues_filter(data, args.output_dir)
    data.pop("venues_by_year", None)
    data.pop("venues_by_catgrp", None)

    # ── Auto-populate trip_names.json with new trips ──────────────────────────
    # Any trip whose _name_ts is not yet in trip_names.json gets added with its
    # auto-generated name + inferred transport icon.
    _ICON_MAP = {
        "Airport":            "✈️",
        "Light Rail Station": "✈️",
        "Rail Station":       "🚂",
        "Train Station":      "🚂",
        "Bus Station":        "🚌",
        "Bus Terminal":       "🚌",
        "Ferry Terminal":     "⛴️",
        "Fuel Station":       "🚗",
        "Gas Station":        "🚗",
        "Parking":            "🚗",
    }
    _ICON_PRIORITY = {"✈️": 4, "🚂": 3, "⛴️": 2, "🚌": 1, "🚗": 0}

    def _infer_icon(trip: dict) -> str:
        # Probe first 5 and last 5 check-ins — transport hubs appended by extension
        checkins = trip.get("checkins", [])
        probe = checkins[:5] + checkins[-5:]
        best = ""
        for ci in probe:
            icon = _ICON_MAP.get(ci.get("category", ""), "")
            if icon and _ICON_PRIORITY.get(icon, -1) > _ICON_PRIORITY.get(best, -1):
                best = icon
        return best

    _ALL_ICONS = {'✈️', '🚂', '🚌', '🚗', '⛺', '🛁', '⛴️'}

    def _base_name(name: str) -> str:
        """Strip any trailing transport icon we may have previously appended."""
        for icon in _ALL_ICONS:
            if name.endswith(" " + icon):
                return name[: -len(icon) - 1]
        return name

    def _name_with_icon(t: dict) -> str:
        # Bicycle trips: template already shows 🚲 badge — no name icon needed.
        if "bicycle" in t.get("tags", []):
            return _base_name(t["name"])
        icon = _infer_icon(t) or "🚗"
        return f"{_base_name(t['name'])} {icon}"

    def _needs_update(ts_key: str, t: dict) -> bool:
        if ts_key not in trip_names:
            return True
        existing = trip_names[ts_key]
        has_icon = any(icon in existing for icon in _ALL_ICONS)
        is_bicycle = "bicycle" in t.get("tags", [])
        # Bicycle trip with a transport icon → strip it
        # Non-bicycle trip missing an icon → add one
        return (is_bicycle and has_icon) or (not is_bicycle and not has_icon)

    new_name_entries = {
        str(t["_name_ts"]): _name_with_icon(t)
        for t in trips
        if _needs_update(str(t["_name_ts"]), t)
    }
    if new_name_entries:
        trip_names.update(new_name_entries)
        trip_names_sorted = dict(sorted(trip_names.items(), key=lambda kv: int(kv[0])))
        trip_names_path.write_text(
            json.dumps(trip_names_sorted, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(
            "Auto-added %d new trip(s) to %s: %s",
            len(new_name_entries),
            trip_names_path,
            ", ".join(f"{k}={v!r}" for k, v in new_name_entries.items()),
        )
        # Patch the in-memory trips list so the HTML reflects the updated names.
        for t in trips:
            ts_key = str(t["_name_ts"])
            if ts_key in new_name_entries:
                t["name"] = new_name_entries[ts_key]

    # ── Write slim trips metadata JSON (for D1 sync) ─────────────────────────
    if args.trips_out:
        _slim_trips = []
        for t in trips:
            _slim_trips.append({
                "id":            t["id"],
                "name":          t["name"],
                "start_date":    t["start_date"],
                "end_date":      t["end_date"],
                "start_ts":      t["start_ts"],
                "start_year":    t["start_year"],
                "duration":      t["duration"],
                "checkin_count": t["checkin_count"],
                "unique_places": t["unique_places"],
                "countries":     t["countries"],
                "cities":        t["cities"],
                "tags":          t.get("tags", []),
                "top_cats":      t.get("top_cats", []),
            })
        Path(args.trips_out).write_text(
            json.dumps(_slim_trips, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Wrote slim trips metadata → %s  (%d trips)", args.trips_out, len(_slim_trips))

    # ── Patch trips with photos per check-in from photos.json (if provided) ──
    _photos_by_checkin: dict = {}
    _pix_dir_uri: str = ""
    if args.photos and Path(args.photos).exists():
        _photos_by_checkin = json.loads(Path(args.photos).read_text(encoding="utf-8"))
        if args.pix_url:
            _pix_dir_uri = args.pix_url.rstrip("/")
        else:
            _pix_dir = Path(args.photos).parent / "pix"
            _pix_dir_uri = _pix_dir.as_uri() if _pix_dir.is_dir() else Path(args.photos).parent.as_uri() + "/pix"
        for t in trips:
            for c in t.get("checkins", []):
                cid = c.get("checkin_id", "")
                c["photos"] = _photos_by_checkin.get(cid, [])
            t["photo_count"] = sum(len(c.get("photos", [])) for c in t.get("checkins", []))

    # ── Load tips for recent section ─────────────────────────────────────────
    # Resolve tips.json next to the input CSV so CI (private-data/checkins.csv →
    # private-data/tips.json) and local (data/checkins.csv → data/tips.json) both work.
    tips_path = Path(args.input).resolve().parent / "tips.json"
    tips_recent_json = '{"total":0,"items":[]}'
    all_tips: list = []
    if tips_path.exists():
        # Import CTRY_NORM from gen_tips for country-name normalisation
        try:
            from gen_tips import CTRY_NORM as _CTRY_NORM
        except ImportError:
            _CTRY_NORM = {}
        _city_merge = mappings.get("city_merge", {})

        all_tips = json.loads(tips_path.read_text(encoding="utf-8"))
        all_tips.sort(key=lambda t: -t.get("ts", 0))
        recent30 = []
        for t in all_tips[:30]:
            ts = t.get("ts", 0)
            date_str = ""
            if ts:
                from datetime import datetime, timezone as _tz
                dt = datetime.fromtimestamp(ts, tz=_tz.utc)
                date_str = dt.strftime("%d %b %Y")
            raw_country = t.get("country") or ""
            raw_city = t.get("city") or ""
            nc = _CTRY_NORM.get(raw_country, raw_country)
            nci = _city_merge.get(raw_city, raw_city)
            recent30.append({
                "id":          t.get("id", ""),
                "ts":          ts,
                "date":        date_str,
                "text":        t.get("text", ""),
                "venue":       t.get("venue", ""),
                "venue_id":    t.get("venue_id", ""),
                "city":        raw_city,
                "country":     raw_country,
                "nc":          nc,
                "nci":         nci,
                "category":    t.get("category", ""),
                "agree_count": t.get("agree_count", 0),
                "closed":      bool(t.get("closed", False)),
                "photo":       (_pix_dir_uri + "/" + t["photo"]) if t.get("photo") and _pix_dir_uri else "",
            })
        tips_recent_json = json.dumps(
            {"total": len(all_tips), "items": recent30},
            ensure_ascii=False
        ).replace("</", "<\\/")
        log.info("Loaded %d tips (recent %d) from %s", len(all_tips), len(recent30), tips_path)

    tips_count = len(all_tips)

    # ── Build per-venue metadata from checkins (first & last visit per venue) ──
    # Used by both ratings and lists loading blocks below.
    _venue_meta: dict = {}
    for r in sorted(rows, key=lambda x: int(x.get("date", 0) or 0)):
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        _ts = int(r.get("date", 0) or 0)
        _first_ts = _venue_meta[vid]["first_ts"] if vid in _venue_meta else _ts
        _venue_meta[vid] = {
            "city":     r.get("city", ""),
            "country":  r.get("country", ""),
            "category": r.get("category", ""),
            "lat":      r.get("lat", ""),
            "lng":      r.get("lng", ""),
            "last_ts":  _ts,
            "first_ts": _first_ts,
        }

    # Build closed-venue set from tips
    _closed_venues: set = {
        t.get("venue_id", "") for t in all_tips
        if t.get("closed") and t.get("venue_id")
    }

    # ── Load venue ratings for index.html feed and ratings.html ──────────────
    ratings_path = Path(args.ratings) if args.ratings else Path(args.input).resolve().parent / "venueRatings.json"
    ratings_recent_json = "[]"
    ratings_counts_json = '{"likes":0,"neutral":0,"dislikes":0}'
    _all_ratings: dict = {"venueLikes": [], "venueOkays": [], "venueDislikes": []}
    if ratings_path.exists():
        _all_ratings = json.loads(ratings_path.read_text(encoding="utf-8"))

        def _enrich(entries: list, rating: str) -> list:
            result = []
            for i, e in enumerate(entries):
                vid = e.get("id", "").strip()
                if not vid:
                    continue
                meta = _venue_meta.get(vid, {})
                raw_country = meta.get("country", "")
                raw_city = meta.get("city", "")
                nc  = _CTRY_NORM.get(raw_country, raw_country)
                nci = _city_merge.get(raw_city, raw_city)
                last_ts = meta.get("last_ts", 0)
                date_str = ""
                if last_ts:
                    from datetime import datetime, timezone as _tz
                    date_str = datetime.fromtimestamp(last_ts, tz=_tz.utc).strftime("%d %b %Y")
                created_at = int(e.get("createdAt") or 0) or int(meta.get("first_ts") or 0)
                result.append({
                    "id":         vid,
                    "name":       e.get("name", ""),
                    "url":        e.get("url", ""),
                    "rating":     rating,
                    "city":       raw_city,
                    "country":    raw_country,
                    "nc":         nc,
                    "nci":        nci,
                    "category":   meta.get("category", ""),
                    "lat":        meta.get("lat", ""),
                    "lng":        meta.get("lng", ""),
                    "last_ts":    last_ts,
                    "last_date":  date_str,
                    "closed":     vid in _closed_venues,
                    "rate_idx":   i,
                    "created_at": created_at,
                })
            # Preserve the source list order (rate_idx = position in venueRatings.json).
            # For API-fetched items this IS the exact chronological like order
            # (index 0 = most recently liked). created_at is kept for year-header display only.
            result.sort(key=lambda x: x["rate_idx"])
            return result

        _likes    = _enrich(_all_ratings.get("venueLikes",   []), "like")
        _neutral  = _enrich(_all_ratings.get("venueOkays",   []), "neutral")
        _dislikes = _enrich(_all_ratings.get("venueDislikes",[]), "dislike")

        ratings_counts_json = json.dumps({
            "likes":   len(_likes),
            "neutral": len(_neutral),
            "dislikes": len(_dislikes),
        })
        ratings_recent_json = json.dumps(_likes[:30], ensure_ascii=False).replace("</", "<\\/")
        log.info("Loaded ratings: %d likes, %d neutral, %d dislikes",
                 len(_likes), len(_neutral), len(_dislikes))
    else:
        _likes = _neutral = _dislikes = []

    # ── Compute total photo count and recent 30 photos for index.html ─────────
    tip_photo_count = sum(1 for t in all_tips if t.get("photo")) if _pix_dir_uri else 0
    total_photos = (sum(len(v) for v in _photos_by_checkin.values()) if _photos_by_checkin else 0) + tip_photo_count
    recent_photos_json = "[]"
    if _photos_by_checkin and _pix_dir_uri:
        _photo_rows = [
            (r, _photos_by_checkin[r.get("checkin_id", "")])
            for r in rows
            if r.get("checkin_id", "") in _photos_by_checkin
            and _photos_by_checkin[r.get("checkin_id", "")]
        ]
        _photo_rows.sort(key=lambda x: -int(x[0].get("date", 0) or 0))
        _recent_photos = []
        for _r, _photos in _photo_rows[:30]:
            _ts = int(_r.get("date", 0) or 0)
            _date_str = ""
            if _ts:
                _date_str = datetime.fromtimestamp(_ts, tz=timezone.utc).strftime("%d %b %Y")
            _recent_photos.append({
                "srcs":     [_pix_dir_uri + "/" + f for f in _photos],
                "venue":    _r.get("venue", ""),
                "venue_id": _r.get("venue_id", ""),
                "city":     _r.get("city", ""),
                "date":     _date_str,
            })
        recent_photos_json = json.dumps(_recent_photos, ensure_ascii=False).replace("</", "<\\/")

    # ── Load lists.json for lists.html ───────────────────────────────────────
    lists_path = Path(args.lists) if args.lists else Path(args.input).resolve().parent / "lists.json"
    _lists_data_json = "[]"
    if lists_path.exists():
        try:
            _raw_lists = json.loads(lists_path.read_text(encoding="utf-8"))
            _raw_items = _raw_lists if isinstance(_raw_lists, list) else _raw_lists.get("items", [])
            # Build rating lookup: venue_id → rating string
            _rating_lookup: dict = {}
            for _rv in _all_ratings.get("venueLikes",   []):
                if _rv.get("id"): _rating_lookup[_rv["id"]] = "like"
            for _rv in _all_ratings.get("venueOkays",   []):
                if _rv.get("id"): _rating_lookup[_rv["id"]] = "neutral"
            for _rv in _all_ratings.get("venueDislikes",[]):
                if _rv.get("id"): _rating_lookup[_rv["id"]] = "dislike"
            # Build visited set: venue_ids that appear in checkins
            _visited_vids: set = {r.get("venue_id","").strip() for r in rows if r.get("venue_id","").strip()}
            _lists_out = []
            for lst in _raw_items:
                _photo = lst.get("photo") or {}
                _cover = ""
                if _photo.get("prefix") and _photo.get("suffix"):
                    _cover = _photo["prefix"] + "100x100" + _photo["suffix"]
                _list_items = (lst.get("listItems") or {}).get("items") or []
                _venues_out = []
                for li in _list_items:
                    _v = li.get("venue") or {}
                    _vid = str(_v.get("id") or "").strip()
                    if not _vid:
                        continue
                    _meta = _venue_meta.get(_vid, {})
                    _vloc = _v.get("location") or {}
                    _raw_country = _meta.get("country", "") or _vloc.get("country", "")
                    _raw_city    = _meta.get("city", "") or _vloc.get("city", "")
                    _nc  = _CTRY_NORM.get(_raw_country, _raw_country)
                    _nci = _city_merge.get(_raw_city, _raw_city)
                    _last_ts  = _meta.get("last_ts", 0)
                    _first_ts = _meta.get("first_ts", 0)
                    _ld = ""
                    if _first_ts:
                        _ld = datetime.fromtimestamp(_first_ts, tz=timezone.utc).strftime("%d %b %Y")
                    _vout: dict = {"id": _vid, "n": (_v.get("name") or "").strip()}
                    _u = (_v.get("canonicalUrl") or "").strip()
                    if _u: _vout["u"] = _u
                    _cat = _meta.get("category", "")
                    if not _cat:
                        _vcats = _v.get("categories") or []
                        if _vcats:
                            _cat = ((_vcats[0].get("name") or "").strip())
                    if _cat: _vout["cat"] = _cat
                    _lat = _meta.get("lat", "") or str(_vloc.get("lat", "") or "")
                    _lng = _meta.get("lng", "") or str(_vloc.get("lng", "") or "")
                    if _lat: _vout["lat"] = _lat
                    if _lng: _vout["lng"] = _lng
                    if _last_ts: _vout["lts"] = _last_ts
                    if _ld: _vout["ld"] = _ld
                    if _vid in _closed_venues: _vout["cl"] = True
                    _r = _rating_lookup.get(_vid)
                    if _r: _vout["r"] = _r
                    _nc_val = _nc or _raw_country
                    _nci_val = _nci or _raw_city
                    if _nc_val: _vout["nc"] = _nc_val
                    if _nci_val: _vout["nci"] = _nci_val
                    if _raw_city: _vout["city"] = _raw_city
                    if _raw_country: _vout["country"] = _raw_country
                    _vout["visited"] = _vid in _visited_vids
                    _venues_out.append(_vout)
                _upd_raw = lst.get("updatedAt") or 0
                try:
                    _upd_ts = int(_upd_raw)
                except (ValueError, TypeError):
                    try:
                        from datetime import datetime as _dt2
                        _upd_ts = int(_dt2.fromisoformat(str(_upd_raw).replace("Z","")).timestamp())
                    except Exception:
                        _upd_ts = 0
                _lists_out.append({
                    "id":       str(lst.get("id") or ""),
                    "name":     (lst.get("name") or "").strip(),
                    "url":      (lst.get("canonicalUrl") or "").strip(),
                    "cover":    _cover,
                    "count":    len(_venues_out),
                    "venues":   _venues_out,
                    "updatedAt": _upd_ts,
                })
            _lists_data_json = json.dumps(_lists_out, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
            log.info("Loaded %d lists from %s", len(_lists_out), lists_path)
        except Exception as _le:
            log.warning("Failed to load lists.json: %s", _le)
    else:
        log.info("lists.json not found at %s — skipping lists.html", lists_path)

    # ── Load checkins_anomalies.json (generated by fetch_checkins.py) ────────────
    _anomalies_path = Path(args.input).resolve().parent / "checkins_anomalies.json"
    _anomalies_html = ""
    if _anomalies_path.exists():
        try:
            _anom = json.loads(_anomalies_path.read_text(encoding="utf-8"))
            # Use _meta counts (set by fetch_checkins.py at write time) — more reliable
            # than len() which includes stale historical accumulations in "missing".
            _meta = _anom.get("_meta", {})
            _n_dup = _meta.get("duplicates_count", len(_anom.get("duplicates", [])))
            # "missing" is a historical accumulator (grows unboundedly as check-ins age
            # off the API). Don't surface it in the widget — it's not actionable.
            if _n_dup:
                _anomalies_html = (
                    '<div id="anomaly-widget" style="display:flex;gap:12px;align-items:center;'
                    'padding:6px 14px;border-radius:8px;background:rgba(244,131,31,.08);'
                    'border:1px solid rgba(244,131,31,.25);font-family:\'DM Mono\',monospace;'
                    'font-size:.65rem;flex-wrap:wrap;">'
                    f'<span title="Duplicate (venue_id, date) pairs in CSV" style="color:#f4831f">'
                    f'⚠ {_n_dup} duplicate check-in{"s" if _n_dup != 1 else ""}</span>'
                    '</div>'
                )
                log.info("Anomalies: %d duplicate pair(s)", _n_dup)
        except Exception as _ae:
            log.warning("Failed to load anomalies: %s", _ae)

    # ── Extract shouts, then merge per-check-in comment threads ─────────────────
    # comments.json (reply threads under check-ins, distinct from shouts) lives
    # next to checkins.csv. The merged list (shouts + comment-only check-ins,
    # sorted newest-first, threads attached) feeds BOTH the shouts page and the
    # index recent-strip so comments interleave with shouts by date.
    from metrics import shout_records as _shout_records
    from metrics import merge_comments_into_shouts as _merge_comments
    all_shouts = _shout_records(rows)

    _comments_path = Path(args.input).resolve().parent / "comments.json"
    _comments_map: dict = {}
    if _comments_path.exists():
        try:
            _comments_map = json.loads(
                _comments_path.read_text(encoding="utf-8")
            ).get("comments", {}) or {}
        except Exception as _ce:  # noqa: BLE001
            log.warning("Could not read %s (%s) — comments won't render", _comments_path, _ce)
    shouts_for_page = _merge_comments(all_shouts, rows, _comments_map)
    _n_threads = sum(1 for s in shouts_for_page if s.get("comments"))
    log.info("Shouts page: %d entr(y/ies), %d with comment threads (%d comment-only)",
             len(shouts_for_page), _n_threads, len(shouts_for_page) - len(all_shouts))

    # Comment tallies (mine vs others) across every thread — surfaced in the index
    # strip subtitle and the shouts-page insight. "Mine" = comment author matches
    # our Foursquare user id; everything else is a friend's reply.
    _cmt_total = _cmt_mine = _cmt_threads = 0
    for _s in shouts_for_page:
        _cl = _s.get("comments") or []
        if not _cl:
            continue
        _cmt_threads += 1
        for _c in _cl:
            _cmt_total += 1
            if str(_c.get("author_id") or "") == str(fs_user_id or ""):
                _cmt_mine += 1
    _cmt_others = _cmt_total - _cmt_mine
    log.info("Comments: %d total (%d mine, %d others) on %d check-ins",
             _cmt_total, _cmt_mine, _cmt_others, _cmt_threads)

    # Recent strip for index: newest 30 across the merged list (shouts +
    # comment-only), each carrying its comment thread.
    _recent_shouts = []
    for s in shouts_for_page[:30]:
        from datetime import datetime as _dt, timezone as _tz
        try:
            _d = _dt.fromtimestamp(s["ts"], tz=_tz.utc)
            _date_str = _d.strftime("%d %b %Y")
        except (ValueError, OSError):
            _date_str = ""
        _recent_shouts.append({
            "ts":         s["ts"],
            "date":       _date_str,
            "text":       s["text"],
            "venue":      s.get("venue", ""),
            "venue_id":   s.get("venue_id", ""),
            "city":       s.get("city", ""),
            "country":    s.get("country", ""),
            "category":   s.get("category", ""),
            "checkin_id": s.get("checkin_id", ""),
            "companions": s.get("companions", []),
            "comments":   s.get("comments", []),
        })
    shouts_recent_json = json.dumps(
        {"total": len(all_shouts),
         "cmt_total": _cmt_total, "cmt_mine": _cmt_mine,
         "cmt_others": _cmt_others, "cmt_threads": _cmt_threads,
         "items": _recent_shouts},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    log.info("Loaded %d shouts (recent %d)", len(all_shouts), len(_recent_shouts))

    # Load canonical country flag map (English name -> ISO 3166 alpha-2)
    _flags_path = config_dir / "country_flags.json"
    if _flags_path.exists():
        _ctry_flags = json.loads(_flags_path.read_text(encoding="utf-8"))
        # Skip _comment-prefixed keys reserved for inline documentation
        _ctry_flags = {k: v for k, v in _ctry_flags.items() if not k.startswith("_")}
    else:
        _ctry_flags = {}
        log.warning("country_flags.json missing at %s — flag icons will be empty", _flags_path)
    _ctry_flags_json = json.dumps(_ctry_flags, ensure_ascii=False)

    # Load canonical category icon map (category name -> [emoji, color])
    _cat_icon_path = config_dir / "category_icons.json"
    if _cat_icon_path.exists():
        _cat_icons = json.loads(_cat_icon_path.read_text(encoding="utf-8"))
        _cat_icons = {k: v for k, v in _cat_icons.items() if not k.startswith("_")}
    else:
        _cat_icons = {}
        log.warning("category_icons.json missing at %s — category icons will be empty", _cat_icon_path)
    _cat_icons_json = json.dumps(_cat_icons, ensure_ascii=False)

    os.makedirs(args.output_dir, exist_ok=True)
    build(data, trips, out_dir=args.output_dir,
          pix_dir_json=json.dumps(_pix_dir_uri),
          extra_replacements={
              "{{ANOMALIES}}":             _anomalies_html,
              "{{TIPS_RECENT}}":           tips_recent_json,
              "{{TIPS_COUNT}}":            f"{tips_count:,}",
              "{{PHOTOS_KPI}}":            f'<div class="kpi"><div class="num">{total_photos:,}</div><div class="lbl">Photos</div></div>' if total_photos else '',
              "{{PHOTOS_RECENT_JSON}}":    recent_photos_json,
              "{{SWARM_USER_ID}}":         fs_user_id,
              "{{RATINGS_RECENT_JSON}}":   ratings_recent_json,
              "{{RATINGS_COUNTS}}":        ratings_counts_json,
              "{{LISTS_COUNT}}":           str(len(json.loads(_lists_data_json))),
              "{{SHOUTS_RECENT}}":         shouts_recent_json,
              "{{SHOUTS_COUNT}}":          f"{len(all_shouts):,}",
              "{{CTRY_CODE_JSON}}":        _ctry_flags_json,
              "{{CAT_ICON_JSON}}":         _cat_icons_json,
          })

    if args.cat_list:
        save_category_list(rows, os.path.join(args.output_dir, "category_list.txt"))

    # ── Generate companion, feed, world-cities, tips pages ──
    # Templates use {{CTRY_CODE_JSON}} placeholder — substituted in a post-
    # process pass below from the single canonical config/country_flags.json.
    _here = _SCRIPT_DIR
    _gen_outputs: list[str] = []
    for gen_script, gen_out, gen_tmpl, gen_kwargs in [
        (_here / "gen_companions.py", "companions.html",   "companions.html.tmpl",   {"social_data": data}),
        (_here / "gen_feed.py",       "feed.html",         "feed.html.tmpl",         {"swarm_user_id": fs_user_id}),
        (_here / "gen_worldcities.py","world_cities.html", "world_cities.html.tmpl", {"cities_data": data.get("cities"), "country_count": len(data['countries'])}),
        (_here / "gen_venues.py",     "venues.html",       "venues.html.tmpl",       {}),
        (_here / "gen_tips.py",       "tips.html",         "tips.html.tmpl",         {"tips_path": str(tips_path), "pix_url": _pix_dir_uri, "country_count": len(data['countries'])}),
        (_here / "gen_stats.py",      "stats.html",        "stats.html.tmpl",        {"stats_data": data, "anomalies_html": _anomalies_html}),
        (_here / "gen_search.py",     "search.html",       "search.html.tmpl",       {"rows": rows, "all_tips": all_tips, "trips": trips, "metrics": data}),
        (_here / "gen_ratings.py",    "ratings.html",      "ratings.html.tmpl",      {"likes": _likes, "neutral": _neutral, "dislikes": _dislikes, "country_count": len(data['countries'])}),
        (_here / "gen_lists.py",      "lists.html",        "lists.html.tmpl",        {"lists_data_json": _lists_data_json}),
        (_here / "gen_guide.py",      "guide.html",        "guide.html.tmpl",        {"rows": rows, "mappings": mappings}),
        (_here / "gen_shouts.py",     "shouts.html",       "shouts.html.tmpl",       {"shouts": shouts_for_page, "country_count": len(data['countries']), "swarm_user_id": fs_user_id}),
    ]:
        if gen_script.exists():
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(f"_gen_{gen_script.stem}", gen_script)
            _mod  = _ilu.module_from_spec(_spec)
            try:
                _spec.loader.exec_module(_mod)
                _mod.build_page(
                    csv_path   = args.input,
                    config_dir = str(config_dir),
                    out_path   = os.path.join(args.output_dir, gen_out),
                    tmpl_path  = str(_TEMPLATES_DIR / gen_tmpl),
                    **gen_kwargs,
                )
                _gen_outputs.append(os.path.join(args.output_dir, gen_out))
            except Exception as _e:
                log.warning("Generator %s failed: %s", gen_script.name, _e)
        else:
            log.warning("Generator not found: %s", gen_script)

    # ── Generate flights.html (dedicated page) ───────────────────────────────
    _gen_flights = _SCRIPT_DIR / "gen_flights.py"
    if _gen_flights.exists() and flights_path.exists():
        try:
            import importlib.util as _ilu_f
            _spec_f = _ilu_f.spec_from_file_location("gen_flights", _gen_flights)
            _mod_f  = _ilu_f.module_from_spec(_spec_f)
            _spec_f.loader.exec_module(_mod_f)
            from flights import (
                load_flights as _lf,
                build_iata_coords as _bic,
                summarise as _summ,
            )
            _all_fl2 = _lf(flights_path)
            _iata2 = _bic(rows, _all_fl2)
            _mod_f.build_page(
                csv_path=args.input, config_dir=str(config_dir),
                out_path=os.path.join(args.output_dir, "flights.html"),
                tmpl_path="",
                flights_data=_all_fl2,
                flight_history=_summ(_all_fl2, _iata2),
                iata_coords=_iata2,
            )
        except Exception as _fle:
            log.warning("gen_flights failed: %s", _fle)

    # ── Generate years.html (overview index of year cards) ──────────────────
    _gen_yix = _SCRIPT_DIR / "gen_years_index.py"
    if _gen_yix.exists():
        try:
            import importlib.util as _ilu_yi
            _spec_yi = _ilu_yi.spec_from_file_location("gen_years_index", _gen_yix)
            _mod_yi  = _ilu_yi.module_from_spec(_spec_yi)
            _spec_yi.loader.exec_module(_mod_yi)
            _mod_yi.build_page(
                csv_path=args.input, config_dir=str(config_dir),
                out_path=os.path.join(args.output_dir, "years.html"),
                tmpl_path="",
                stats_data=data, photos_by_checkin=_photos_by_checkin,
                rows=rows, pix_url=_pix_dir_uri,
            )
        except Exception as _yie:
            log.warning("gen_years_index failed: %s", _yie)

    # ── Generate year-YYYY.html album pages ─────────────────────────────────
    _gen_year = _SCRIPT_DIR / "gen_year_pages.py"
    if _gen_year.exists():
        try:
            import importlib.util as _ilu_y
            _spec_y = _ilu_y.spec_from_file_location("gen_year_pages", _gen_year)
            _mod_y  = _ilu_y.module_from_spec(_spec_y)
            _spec_y.loader.exec_module(_mod_y)
            # Load flights once for the year pages (re-parses if available)
            _flights_list = []
            if flights_path.exists():
                try:
                    from flights import load_flights as _load_flights2
                    _flights_list = _load_flights2(flights_path)
                except Exception:
                    pass
            _mod_y.build_page(
                csv_path=args.input,
                config_dir=str(config_dir),
                out_path=args.output_dir,
                tmpl_path="",                           # not used; template inlined
                rows=rows,
                stats_data=data,
                photos_by_checkin=_photos_by_checkin,
                pix_url=_pix_dir_uri,
                trips=trips,
                flight_history=data.get("flight_history"),
                flights_data=_flights_list,
            )
        except Exception as _ye:
            log.warning("gen_year_pages failed: %s", _ye)

    # ── Post-process: substitute canonical placeholders in every generated file ──
    # Single source of truth: config/country_flags.json + config/category_icons.json
    # (both loaded once above).  Done after generators run so we don't need to
    # thread these through every gen_*.py kwarg signature.
    _post_subs = {
        "{{CTRY_CODE_JSON}}": _ctry_flags_json,
        "{{CAT_ICON_JSON}}":  _cat_icons_json,
    }
    for _out_file in _gen_outputs:
        try:
            _p = Path(_out_file)
            if not _p.exists():
                continue
            _txt = _p.read_text(encoding="utf-8")
            _dirty = False
            for _k, _v in _post_subs.items():
                if _k in _txt:
                    _txt = _txt.replace(_k, _v)
                    _dirty = True
            if _dirty:
                _p.write_text(_txt, encoding="utf-8")
        except Exception as _e:
            log.warning("post-process substitution failed for %s: %s", _out_file, _e)

    # ── Generate photos.html (all photos gallery) ────────────────────────────
    if args.photos and _photos_by_checkin:
        gen_photos_script = _SCRIPT_DIR / "gen_photos.py"
        if gen_photos_script.exists():
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("gen_photos", gen_photos_script)
            _mod  = _ilu.module_from_spec(_spec)
            try:
                _spec.loader.exec_module(_mod)
                _mod.build_page(
                    photos_by_checkin=_photos_by_checkin,
                    csv_path=args.input,
                    rows=rows,
                    pix_dir_uri=_pix_dir_uri,
                    out_path=os.path.join(args.output_dir, "photos.html"),
                    tips=all_tips if _pix_dir_uri else [],
                    city_merge=mappings.get("city_merge", {}),
                    ctry_norm=_CTRY_NORM,
                    country_count=len(data['countries']),
                )
                # Post-process the canonical placeholders.
                _photos_html = Path(args.output_dir) / "photos.html"
                if _photos_html.exists():
                    _txt = _photos_html.read_text(encoding="utf-8")
                    _dirty = False
                    for _k, _v in _post_subs.items():
                        if _k in _txt:
                            _txt = _txt.replace(_k, _v)
                            _dirty = True
                    if _dirty:
                        _photos_html.write_text(_txt, encoding="utf-8")
            except Exception as _e:
                log.warning("gen_photos.py failed: %s", _e)
        else:
            log.warning("gen_photos.py not found — skipping photos.html")

    log.info("Done!")