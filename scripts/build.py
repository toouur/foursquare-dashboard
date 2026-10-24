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
from metrics import compute_coverage, compute_milestones, process
from metrics.trip_insights import trip_used_plane

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


def generate_feed_meta(rows: list[dict], output_dir: str, flight_days: list[str] | None = None):
    """Create feed_meta.json with ym_index, total, first_ts, flight_days.
    Replaces expensive D1 query for month index.  flight_days (sorted
    "YYYY-MM-DD" strings from the FR24 diary) feeds the feed's client-side
    transport-mode classifier.
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
        "flight_days": flight_days or [],
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

# The only keys trips.html reads off its `const S` (all accessed as S.<key>).
_TRIPS_STATS_KEYS = (
    'new_country_by_year', 'total_km', 'trip_countries_dist', 'trip_duration_hist',
    'trip_eras', 'trip_gateways', 'trip_kpis', 'trip_seasonality', 'trip_top_longest',
    'trips_count',
)
# Check-in fields shipped in the trip shards, positionally. `datetime` is dropped
# (the page never reads it); `refurbished` is stored as 1/0.
_TRIP_CK_FIELDS = ('ts', 'date', 'time', 'venue', 'venue_id', 'city', 'country',
                   'category', 'lat', 'lng', 'checkin_id', 'refurbished', 'photos', 'm')
_TRIP_SHARD = 10


def _write_trip_checkin_shards(trips, out_dir):
    """Write trip_checkins_<id//10>.json; return (content version, trips without checkins)."""
    import glob
    import hashlib
    for stale in glob.glob(os.path.join(out_dir, 'trip_checkins_*.json')):
        os.remove(stale)
    shards: dict = {}
    inline = []
    for t in trips:
        rows = []
        for c in t.get('checkins') or []:
            row = [c.get(k) for k in _TRIP_CK_FIELDS]
            row[_TRIP_CK_FIELDS.index('refurbished')] = 1 if c.get('refurbished') else 0
            rows.append(row)
        shards.setdefault(t['id'] // _TRIP_SHARD, {})[str(t['id'])] = rows
        inline.append({k: v for k, v in t.items() if k != 'checkins'})
    h = hashlib.sha1()
    for n in sorted(shards):
        body = json.dumps({'f': _TRIP_CK_FIELDS, 't': shards[n]},
                          ensure_ascii=False, separators=(',', ':'))
        h.update(body.encode('utf-8'))
        with open(os.path.join(out_dir, f'trip_checkins_{n}.json'), 'w', encoding='utf-8') as f:
            f.write(body)
    return h.hexdigest()[:8], inline


def build(data, trips, out_dir='.', extra_replacements=None, pix_dir_json='""'):
    import os
    # ── Externalize the heavy below-the-fold map payload ────────────────────
    # unique_places (~2.8 MB) + venues_heatmap (~1.4 MB) + explorer_groups +
    # country_centroids feed ONLY the Leaflet map card, which is lazy-built on
    # first view. Ship them in map_data.json (fetched on demand) instead of
    # inlining into every index/trips page's {{STATS}} const S={...}.
    _MAP_KEYS = ('unique_places', 'venues_heatmap', 'explorer_groups', 'country_centroids')
    map_payload = {k: data[k] for k in _MAP_KEYS if k in data}
    with open(os.path.join(out_dir, 'map_data.json'), 'w', encoding='utf-8') as f:
        json.dump(map_payload, f, ensure_ascii=False, separators=(',', ':'))
    # STATS without the map keys — do NOT mutate `data` (later generators read it).
    stats_slim = {k: v for k, v in data.items() if k not in _MAP_KEYS}
    stats_json = json.dumps(stats_slim, ensure_ascii=False).replace('</', '<\\/')

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
    _rf_ci = data.get('refurb_checkins', 0)
    _rf_pl = data.get('refurb_places', 0)
    _rf_note = (
        f'<div class="kpi-note">↺ {_rf_ci:,} reconstructed check-ins'
        f' &middot; +{_rf_pl:,} unique places</div>'
    ) if _rf_ci else ''
    html = html.replace('{{RECONSTRUCTED_NOTE}}', _rf_note)
    html = html.replace('{{STATS}}',     stats_json)
    if extra_replacements:
        for key, val in extra_replacements.items():
            html = html.replace(key, val)
    idx_path = os.path.join(out_dir, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f: f.write(html)
    print(f"Built ->{idx_path}  ({len(html)//1024:,} KB)")

    # ── trips.html ──────────────────────────────────────────────────────────
    # The per-trip check-in lists (~18 MB of a ~21 MB page) are opened one trip
    # at a time, so they ship as trip_checkins_<id//10>.json shards fetched on
    # demand; the page inlines only trip metadata and the stats it charts.
    tck_ver, trips_inline = _write_trip_checkin_shards(trips, out_dir)
    trips_stats_json = json.dumps({k: data[k] for k in _TRIPS_STATS_KEYS if k in data},
                                  ensure_ascii=False).replace('</', '<\\/')
    trips_html = TRIPS_TEMPLATE
    trips_html = trips_html.replace('{{TRIPS_JSON}}', json.dumps(trips_inline, ensure_ascii=False).replace('</', '<\\/'))
    trips_html = trips_html.replace('{{TRIP_CK_VER}}', tck_ver)
    trips_html = trips_html.replace('{{TOTAL_TRIPS}}', str(len(trips)))
    trips_html = trips_html.replace('{{COUNTRIES}}', str(len(data['countries'])))
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    trips_html = trips_html.replace('{{PIX_DIR_JSON}}', pix_dir_json)
    trips_html = trips_html.replace('{{STATS}}', trips_stats_json)
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
    parser.add_argument("--trip-names-healed-out", default=None,
                        help="When orphaned trip_names keys were healed (a trip start "
                             "moved), also write the healed trip_names.json here — "
                             "WITHOUT this build's auto-added names, so CI can commit "
                             "just the heal. Emits TRIP_NAMES_HEALED=<n>.")
    parser.add_argument("--travel-atlas-out", default=None,
                        help="Opt-in: also write the standalone travel-atlas journal "
                             "(scripts/gen_travel_atlas.py) to this path, built from the "
                             "same rows/stats/trips as the site. Needs --photos.")
    parser.add_argument("--routes-cache", default=None,
                        help="Path to routes_cache.json (persistent OSRM/BRouter "
                             "route-geometry cache, lives in the private data repo). "
                             "When set, trip-map segments get real street/rail paths; "
                             "omitted → routes.json is empty and the map draws "
                             "straight lines.")
    parser.add_argument("--routes-max-fetch", type=int, default=250,
                        help="Max missing routes to fetch per build (default 250); "
                             "the cache warms up over successive CI builds.")
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

    # Era-aware home: each home_history entry names the home BEFORE its `until`
    # date (exclusive, UTC midnight). Parse to sorted [(until_ts, city)] so
    # detect_trips can bucket pre-move check-ins (e.g. 2008 Chișinău) as home,
    # not as a phantom trip. See config/settings.yaml trip_detection.home_history.
    # home_venue_ids: venue_ids flagged as a home across any era (from each
    # home_history entry's optional `venues:`). A check-in at one is treated as
    # the homecoming by detect_trips' arrival extension regardless of category.
    # home_eras carries the SAME entries with their per-era trip rules attached
    # (air_departure / arrival_transit / arrival_neighborhoods / venues), so
    # detect_trips can branch by date instead of applying one rule to twenty
    # years. An era that sets no flags keeps the original behaviour exactly —
    # which is how the Minsk years stay frozen while the Chișinău era gets the
    # airport rules. See config/settings.yaml trip_detection.home_history.
    home_history: list[tuple[int, str]] = []
    home_venue_ids: set[str] = set()
    home_eras: list[tuple[int, dict]] = []
    for _hh in trip_cfg.get("home_history", []) or []:
        _city = str(_hh.get("city", "")).strip()
        _until = str(_hh.get("until", "")).strip()
        _era_venues: set[str] = set()
        for _hv in _hh.get("venues", []) or []:
            _hv = str(_hv).strip()
            if _hv:
                home_venue_ids.add(_hv)
                _era_venues.add(_hv)
        if not _city or not _until:
            continue
        _until_ts = int(datetime.strptime(_until, "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp())
        home_history.append((_until_ts, _city))
        home_eras.append((_until_ts, {
            "city": _city,
            "venues": _era_venues,
            "air_departure": bool(_hh.get("air_departure")),
            "arrival_transit": bool(_hh.get("arrival_transit")),
            "arrival_neighborhoods": {
                str(_n).strip() for _n in (_hh.get("arrival_neighborhoods") or []) if str(_n).strip()
            },
            "departure_ride_categories": {
                str(_c).strip() for _c in (_hh.get("departure_ride_categories") or []) if str(_c).strip()
            },
            "arrival_stop_at_home": bool(_hh.get("arrival_stop_at_home")),
            "arrival_max_city_checkins": int(_hh.get("arrival_max_city_checkins") or 0),
            "hub_categories": {
                str(_c).strip() for _c in (_hh.get("hub_categories") or []) if str(_c).strip()
            },
        }))
    home_eras.sort(key=lambda e: e[0])
    home_history.sort(key=lambda p: p[0])

    log.info("Loading mappings from %s …", config_dir)
    mappings = load_mappings(config_dir)

    log.info("Reading %s …", args.input)
    with open(args.input, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    log.info("  %d rows loaded", len(rows))

    # Merge reconstructed pre-2012 travel history (backfill.csv), if present next
    # to the input CSV. These rows are stamped source_app="refurbished" and flow
    # through the same transforms + generators as real check-ins, so every KPI /
    # story / map / trip shifts automatically. They are badged as reconstructed
    # wherever an individual check-in card renders. See scripts/build_backfill.py.
    _backfill_csv = Path(args.input).resolve().parent / "backfill.csv"
    if _backfill_csv.exists():
        with open(_backfill_csv, encoding="utf-8") as fh:
            _bf_rows = list(csv.DictReader(fh))
        rows.extend(_bf_rows)
        rows.sort(key=lambda r: int(r.get("date") or 0))
        log.info("  + %d reconstructed row(s) from %s (%d total)",
                 len(_bf_rows), _backfill_csv.name, len(rows))

    # Blank-city inference: if the review CSV exists next to the config dir,
    # resolve blank city fields using timestamp + coordinate matching.
    review_csv = config_dir / "city_merge_normalized_review.csv"
    blank_resolver = build_blank_city_resolver(review_csv)

    rows = apply_transforms(rows, mappings, blank_city_resolver=blank_resolver)

    # ── Generate static feed_meta.json (replaces expensive D1 query) ─────────
    # FR24 flight calendar days ride along for the feed's client-side
    # transport-mode classifier (flights.csv lives next to checkins.csv).
    _flights_csv = Path(args.input).resolve().parent / "flights.csv"
    _flight_days: list[str] = []
    if _flights_csv.exists():
        try:
            from flights import load_flights as _lf_days
            _flight_days = sorted({f["date"] for f in _lf_days(_flights_csv) if f.get("date")})
        except Exception as _fde:
            log.warning("flight days for feed_meta failed: %s", _fde)
    generate_feed_meta(rows, args.output_dir, _flight_days)

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

    # Real FR24 flight diary, threaded into metrics so the per-year narrative
    # flight counts + transport-mode classifier reconcile against actual flights
    # (unmatched fast hops are demoted from "flight").
    _metrics_flights: list[dict] = []
    _metrics_flights_csv = Path(args.input).resolve().parent / "flights.csv"
    if _metrics_flights_csv.exists():
        try:
            from flights import load_flights as _lf_metrics
            _metrics_flights = _lf_metrics(_metrics_flights_csv)
        except Exception as _mfe:
            log.warning("flights.csv load for metrics failed: %s", _mfe)

    log.info("Computing metrics (home=%s, min_checkins=%d) …", home_city, min_checkins)
    _name_report: dict = {}
    data, trips = process(rows, mappings, home_city=home_city, min_trip_checkins=min_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags, new_country_year_overrides=nc_yr_overrides, flights=_metrics_flights, home_history=home_history, home_venue_ids=home_venue_ids, home_eras=home_eras, trip_name_report=_name_report)
    try:
        data["milestones"] = compute_milestones(rows, data.get("distance_from_home"))
    except Exception as _mse:
        log.warning("milestones failed: %s", _mse)
    try:
        data["coverage"] = compute_coverage(rows, home_city, home_history)
    except Exception as _cve:
        log.warning("coverage failed: %s", _cve)

    # ── trip_names.json self-heal (see metrics/trip_names.py) ─────────────────
    # detect_trips already rendered the healed names; here the heal is logged,
    # adopted for the auto-populate write below, and — for CI — written on its
    # own (no auto-added names) so only the heal itself gets committed.
    _heal = _name_report.get("heal")
    _healed_n = 0
    if _heal is not None:
        from metrics.trip_names import describe as _describe_heal
        for _line in _describe_heal(_heal):
            log.warning("trip_names: %s", _line)
        _healed_n = _heal.changed
        if _healed_n:
            trip_names = dict(_heal.names)
            if args.trip_names_healed_out:
                Path(args.trip_names_healed_out).write_text(json.dumps(
                    dict(sorted(trip_names.items(), key=lambda kv: int(kv[0]))),
                    ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TRIP_NAMES_HEALED={_healed_n}")
    print(f"TRIP_NAMES_SUSPICIOUS={len(_heal.suspicious) if _heal else 0}")
    _gho = os.environ.get("GITHUB_OUTPUT")
    if _gho:
        with open(_gho, "a", encoding="utf-8") as _fh:
            _fh.write(f"TRIP_NAMES_HEALED={_healed_n}\n")
            _fh.write(f"TRIP_NAMES_SUSPICIOUS={len(_heal.suspicious) if _heal else 0}\n")

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

    # ── Auto-populate trip_names.json with new trips ──────────────────────────
    # Any trip whose _name_ts is not yet in trip_names.json gets added with its
    # auto-generated name + inferred transport icon.
    # The airport family below matters because Foursquare almost never emits the
    # bare "Airport" category — 28 of ~1000 airport check-ins carry it, Chișinău's
    # RMO none at all ("International Airport"). Without these every flight out
    # of Chișinău got the 🚗 fallback, and flight trips had to be fixed to ✈️ by
    # hand in trip_names.json. Only NEW trips are affected: an existing entry
    # that already has an icon is never rewritten (see _needs_update).
    _ICON_MAP = {
        "Airport":                "✈️",
        "International Airport":  "✈️",
        "Airport Terminal":       "✈️",
        "Airport Gate":           "✈️",
        "Airport Service":        "✈️",
        "Airport Lounge":         "✈️",
        "Airport Ticket Counter": "✈️",
        "Airport Tram Station":   "✈️",
        "Baggage Claim":          "✈️",
        "Plane":                  "✈️",
        "Light Rail Station":     "✈️",
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
        checkins = trip.get("checkins", [])
        # A flight anywhere in the trip wins — the mode that took you furthest.
        # Only a real flight counts: an airport visit with the same city on
        # both sides (seeing someone off) does not, and it never earns ✈️
        # from the probe below either.
        if trip_used_plane(checkins):
            return "✈️"
        # Probe first 5 and last 5 check-ins — transport hubs appended by extension
        probe = checkins[:5] + checkins[-5:]
        best = ""
        for ci in probe:
            icon = _ICON_MAP.get(ci.get("category", ""), "")
            if icon == "✈️":
                continue
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
    if new_name_entries or _healed_n:
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

    # ── Per-segment transport modes (trips map + feed) ───────────────────────
    # Attach checkin["m"] (arrival-mode char) to every trip check-in before
    # the TRIPS_JSON dump.  Rule cascade + a Naive Bayes layer re-trained on
    # this build's own rule-anchored segments (see scripts/transport_mode.py).
    try:
        import transport_mode as _tm
        _fl_modes = []
        if flights_path.exists():
            from flights import load_flights as _lf_modes
            _fl_modes = _lf_modes(flights_path)
        _tm_windows = _tm.flight_windows(_fl_modes)
        _tm_bikes = ["bicycle" in t.get("tags", []) for t in trips]
        _tm_model = _tm.train_model(
            [t.get("checkins", []) for t in trips], _tm_windows, _tm_bikes)
        from collections import Counter as _Counter
        _tm_counts = _Counter()
        for t, _bike in zip(trips, _tm_bikes):
            _modes = _tm.classify_segments(
                t.get("checkins", []), _tm_windows, bike=_bike, model=_tm_model)
            for c, m in zip(t.get("checkins", []), _modes):
                if m:
                    c["m"] = m
                    _tm_counts[m] += 1
        log.info("Transport modes tagged: %s (NB layer: %s)",
                 dict(_tm_counts.most_common()),
                 "self-trained" if _tm_model else "fallback to bands")
    except Exception as _tme:
        log.warning("transport-mode classification failed: %s", _tme)

    # ── GPS-like route geometry for trip-map segments (routes.json) ──────────
    # Fetch real street/rail paths (FOSSGIS OSRM + BRouter) for mode-tagged
    # segments, served from a persistent cache in the private data repo.
    # routes.json is ALWAYS written (empty when --routes-cache is unset) so the
    # template's fetch never 404s.
    _routes: dict = {}
    if args.routes_cache:
        try:
            import route_paths as _rp
            _rc = _rp.RouteCache(args.routes_cache)
            _routes = _rp.attach_routes(trips, _rc, max_fetch=args.routes_max_fetch)
            _rc.save()
            log.info("Route geometry: %d segments routed (cache %s: %d entries)",
                     len(_routes), args.routes_cache, len(_rc.data))
        except Exception as _rpe:
            log.warning("route-geometry attachment failed: %s", _rpe)
    os.makedirs(args.output_dir, exist_ok=True)
    Path(os.path.join(args.output_dir, "routes.json")).write_text(
        json.dumps(_routes, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

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
            assert _spec and _spec.loader
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
            assert _spec_f and _spec_f.loader
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
            assert _spec_yi and _spec_yi.loader
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

    # ── Cultural chronicle: IMDb + Vivino + Last.fm joined to check-ins ───────
    # Sources sit next to checkins.csv (missing ones just mean "no culture").
    # Computed before the year pages, which carry a "Year in culture" section;
    # culture.html and the trip modal read the same result later.
    _culture: dict = {}
    try:
        from culture_sources import load_imdb_any, load_scrobbles, load_vivino_any
        from metrics.culture import compute_culture
        _cdir = Path(args.input).resolve().parent
        _culture = compute_culture(
            rows, trips,
            films=load_imdb_any(_cdir),
            wines=load_vivino_any(_cdir),
            scrobbles=load_scrobbles(_cdir / "lastfm"),
        )
    except Exception as _cue:
        log.warning("culture failed: %s", _cue)

    # ── Generate year-YYYY.html album pages ─────────────────────────────────
    _gen_year = _SCRIPT_DIR / "gen_year_pages.py"
    if _gen_year.exists():
        try:
            import importlib.util as _ilu_y
            _spec_y = _ilu_y.spec_from_file_location("gen_year_pages", _gen_year)
            assert _spec_y and _spec_y.loader
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
            # Last.fm per-year music aggregate (sibling of checkins.csv, like flights.csv).
            _music_by_year: dict = {}
            _lastfm_path = Path(args.input).resolve().parent / "lastfm_years.json"
            if _lastfm_path.exists():
                try:
                    _lf_music = json.loads(_lastfm_path.read_text(encoding="utf-8"))
                    _music_by_year = _lf_music.get("years", {}) or {}
                    log.info("Last.fm: %d years of scrobble stats loaded", len(_music_by_year))
                except Exception as _lfe:
                    log.warning("lastfm_years.json load failed: %s", _lfe)
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
                music_by_year=_music_by_year,
                culture_by_year=_culture.get("years", {}),
            )
        except Exception as _ye:
            log.warning("gen_year_pages failed: %s", _ye)

    # ── Generate country-<slug>.html per-country pages ──────────────────────────
    _gen_ctry = _SCRIPT_DIR / "gen_country_pages.py"
    if _gen_ctry.exists():
        try:
            import importlib.util as _ilu_c
            _spec_c = _ilu_c.spec_from_file_location("gen_country_pages", _gen_ctry)
            assert _spec_c and _spec_c.loader
            _mod_c  = _ilu_c.module_from_spec(_spec_c)
            _spec_c.loader.exec_module(_mod_c)
            _mod_c.build_page(
                csv_path=args.input,
                config_dir=str(config_dir),
                out_path=args.output_dir,
                tmpl_path="",                           # not used; template inlined
                rows=rows,
                stats_data=data,
                photos_by_checkin=_photos_by_checkin,
                pix_url=_pix_dir_uri,
                trips=trips,
            )
        except Exception as _ce:
            log.warning("gen_country_pages failed: %s", _ce)

    # ── Generate venue.html + venue_data_<xx>.json shards ──────────────────────
    _gen_venue = _SCRIPT_DIR / "gen_venue_page.py"
    if _gen_venue.exists():
        try:
            import importlib.util as _ilu_v
            _spec_v = _ilu_v.spec_from_file_location("gen_venue_page", _gen_venue)
            assert _spec_v and _spec_v.loader
            _mod_v  = _ilu_v.module_from_spec(_spec_v)
            _spec_v.loader.exec_module(_mod_v)
            _mod_v.build_page(
                out_path=args.output_dir,
                rows=rows,
                photos_by_checkin=_photos_by_checkin,
                pix_url=_pix_dir_uri,
            )
        except Exception as _ve:
            log.warning("gen_venue_page failed: %s", _ve)

    # ── Generate compare.html + year_facts.json (years / home eras side by side) ─
    _gen_cmp = _SCRIPT_DIR / "gen_compare.py"
    if _gen_cmp.exists():
        try:
            import importlib.util as _ilu_cmp
            _spec_cmp = _ilu_cmp.spec_from_file_location("gen_compare", _gen_cmp)
            assert _spec_cmp and _spec_cmp.loader
            _mod_cmp  = _ilu_cmp.module_from_spec(_spec_cmp)
            _spec_cmp.loader.exec_module(_mod_cmp)
            _mod_cmp.build_page(
                out_path=args.output_dir,
                rows=rows,
                trips=trips,
                stats_data=data,
                photos_by_checkin=_photos_by_checkin,
                home_city=home_city,
                home_history=home_history,
            )
        except Exception as _cmpe:
            log.warning("gen_compare failed: %s", _cmpe)

    # ── Generate life.html + life_data.json ("Life in stripes") ─────────────────
    _gen_life = _SCRIPT_DIR / "gen_life.py"
    if _gen_life.exists():
        try:
            import importlib.util as _ilu_l
            _spec_l = _ilu_l.spec_from_file_location("gen_life", _gen_life)
            assert _spec_l and _spec_l.loader
            _mod_l  = _ilu_l.module_from_spec(_spec_l)
            _spec_l.loader.exec_module(_mod_l)
            _life_music: dict = {}
            _life_lf = Path(args.input).resolve().parent / "lastfm_years.json"
            if _life_lf.exists():
                _life_music = json.loads(_life_lf.read_text(encoding="utf-8")).get("years", {}) or {}
            _mod_l.build_page(
                out_path=args.output_dir,
                rows=rows,
                trips=trips,
                home_city=home_city,
                home_history=home_history,
                music_by_year=_life_music,
            )
        except Exception as _le:
            log.warning("gen_life failed: %s", _le)

    # ── Generate culture.html + culture_data.json (sound · taste · frame) ─────
    _gen_cult = _SCRIPT_DIR / "gen_culture.py"
    if _gen_cult.exists():
        try:
            import importlib.util as _ilu_cu
            _spec_cu = _ilu_cu.spec_from_file_location("gen_culture", _gen_cult)
            assert _spec_cu and _spec_cu.loader
            _mod_cu  = _ilu_cu.module_from_spec(_spec_cu)
            _spec_cu.loader.exec_module(_mod_cu)
            _cu_music: dict = {}
            _cu_lf = Path(args.input).resolve().parent / "lastfm_years.json"
            if _cu_lf.exists():
                _cu_music = json.loads(_cu_lf.read_text(encoding="utf-8")).get("years", {}) or {}
            _mod_cu.build_page(
                out_path=args.output_dir,
                culture=_culture,
                music_by_year=_cu_music,
                profiles=settings.get("culture") or {},
            )
        except Exception as _cue2:
            log.warning("gen_culture failed: %s", _cue2)

    # ── visits.html: the owner's private view of /api/visits (STATS_KEY-gated) ──
    try:
        import gen_visits
        gen_visits.build_page(out_path=args.output_dir)
    except Exception as _vse:
        log.warning("gen_visits failed: %s", _vse)

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
            assert _spec and _spec.loader
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

    # ── Inject shared site.css (content-hashed) ───────────────────────────────
    # assets/site.css holds the byte-identical side-nav rail pulled out of the 12
    # page templates. Copy it into the output root under a content-hashed name and
    # replace the {{SITE_CSS_LINK}} placeholder in EVERY generated page in one pass
    # — index/trips (built via build()), all _gen_outputs, photos, years, trips and
    # flights — so no page can ship an unsubstituted placeholder (which would fail
    # validate_html). Pages without the placeholder (year/trip pages that still
    # inline their nav CSS) are simply left untouched.
    _site_css_src = _PROJECT_ROOT / "assets" / "site.css"
    if _site_css_src.exists():
        import hashlib as _hashlib
        _css_bytes = _site_css_src.read_bytes()
        _css_hash = _hashlib.sha1(_css_bytes).hexdigest()[:8]
        _css_name = f"site-{_css_hash}.css"
        (Path(args.output_dir) / _css_name).write_bytes(_css_bytes)
        _n_linked = 0
        for _html in Path(args.output_dir).glob("*.html"):
            _txt = _html.read_text(encoding="utf-8")
            if "{{SITE_CSS_LINK}}" in _txt:
                _html.write_text(_txt.replace("{{SITE_CSS_LINK}}", _css_name), encoding="utf-8")
                _n_linked += 1
        log.info("Injected %s into %d pages", _css_name, _n_linked)
    else:
        log.warning("assets/site.css not found — {{SITE_CSS_LINK}} left unsubstituted")

    # ── CARTO basemap key ─────────────────────────────────────────────────────
    # Since Sep 2026 basemaps.cartocdn.com answers keyless requests with an
    # "API KEY REQUIRED" watermark tile. Every Leaflet tile URL carries
    # ?key={{CARTO_KEY}}; fill it in every page (the key is public by design —
    # it ships in the browser — and is domain-restricted on CARTO's side).
    # env CARTO_API_KEY wins over settings.yaml basemap.carto_key.
    _carto_key = (os.environ.get("CARTO_API_KEY")
                  or (settings.get("basemap") or {}).get("carto_key") or "").strip()
    if not _carto_key:
        log.warning("No CARTO key (settings.yaml basemap.carto_key / env CARTO_API_KEY) — maps will show the CARTO watermark")
    _n_keyed = 0
    for _html in Path(args.output_dir).glob("*.html"):
        _txt = _html.read_text(encoding="utf-8")
        if "{{CARTO_KEY}}" in _txt:
            _html.write_text(_txt.replace("{{CARTO_KEY}}", _carto_key), encoding="utf-8")
            _n_keyed += 1
    log.info("CARTO key injected into %d pages", _n_keyed)

    # ── PWA: manifest + service worker (versioned per build) ──────────────────
    # Render templates/{manifest.webmanifest,sw.js}.tmpl into the output root,
    # stamping the SW cache name with this build's UTC timestamp so every deploy
    # invalidates the previous service-worker cache. Then inject the manifest
    # link + theme-color + SW registration into every generated page before
    # </head> (idempotent — skips a page that already carries the link).
    _sw_version = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    _manifest_src = _TEMPLATES_DIR / "manifest.webmanifest.tmpl"
    _sw_src = _TEMPLATES_DIR / "sw.js.tmpl"
    if _manifest_src.exists() and _sw_src.exists():
        (Path(args.output_dir) / "manifest.webmanifest").write_text(
            _manifest_src.read_text(encoding="utf-8"), encoding="utf-8")
        (Path(args.output_dir) / "sw.js").write_text(
            _sw_src.read_text(encoding="utf-8").replace("{{SW_VERSION}}", _sw_version),
            encoding="utf-8")
        _pwa_head = (
            '<link rel="manifest" href="/manifest.webmanifest">'
            '<meta name="theme-color" content="#0b0d13">'
            "<script>if('serviceWorker'in navigator){"
            "addEventListener('load',function(){"
            "navigator.serviceWorker.register('/sw.js').catch(function(){})})}</script>"
        )
        _n_pwa = 0
        for _html in Path(args.output_dir).glob("*.html"):
            _txt = _html.read_text(encoding="utf-8")
            if 'rel="manifest"' in _txt or "</head>" not in _txt:
                continue
            _html.write_text(_txt.replace("</head>", _pwa_head + "</head>", 1), encoding="utf-8")
            _n_pwa += 1
        log.info("PWA: manifest + sw.js (v%s) injected into %d pages", _sw_version, _n_pwa)
    else:
        log.warning("PWA templates missing — skipping manifest/sw.js")

    # ── Cloudflare Web Analytics beacon on EVERY page ──────────────────────────
    # It used to live in 10 hand-edited templates only, so year/country/trip/
    # venue/compare/life/culture pages were never counted. Pages already carrying
    # it (the templates) are left alone; Google verification stubs have no body.
    _cf_beacon = ("<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
                  "data-cf-beacon='{\"token\": \"096ba3d9093f4c629948d81ef8438a34\"}'></script><!-- End Cloudflare Web Analytics -->")
    # Plus our own counter (functions/api/hit.js → D1 `visits`, private view in
    # visits.html): one sendBeacon per page view with the path, referrer, a random
    # visitor id from localStorage and the owner flag. Every failure is swallowed.
    _hit_js = ("<script>(function(){try{var s=localStorage,v=s.getItem('fsq_vid');"
               "if(!v){v=Math.random().toString(36).slice(2)+Date.now().toString(36);s.setItem('fsq_vid',v)}"
               "var p=location.pathname+(/venue\\.html$/.test(location.pathname)?location.hash:'');"
               "navigator.sendBeacon('/api/hit',JSON.stringify({p:p,r:document.referrer||'',v:v,"
               "o:s.getItem('fsq_owner')==='1'?1:0}))}catch(e){}})();</script>")
    _n_beacon = _n_hit = 0
    for _html in Path(args.output_dir).glob("*.html"):
        _txt = _html.read_text(encoding="utf-8")
        if "</body>" not in _txt:
            continue
        _add = ""
        if "cloudflareinsights.com/beacon" not in _txt:
            _add += _cf_beacon
            _n_beacon += 1
        if "/api/hit" not in _txt and _html.name != "visits.html":   # the owner's page never counts
            _add += _hit_js
            _n_hit += 1
        if _add:
            _i = _txt.rfind("</body>")
            _html.write_text(_txt[:_i] + _add + _txt[_i:], encoding="utf-8")
    log.info("Web Analytics beacon added to %d more pages, visit counter to %d", _n_beacon, _n_hit)

    # ── Syndication feeds: feed.xml (RSS 2.0) + feed.json (JSON Feed 1.1) ──────
    # Built from the metrics recent[] list (30 newest check-ins). The CI cp glob
    # already ships *.xml/*.json, and both are gitignored like other generated
    # output. index.html links feed.xml via <link rel="alternate">.
    try:
        _gen_feeds = _SCRIPT_DIR / "gen_feeds.py"
        if _gen_feeds.exists():
            import importlib.util as _ilu_gf
            _spec_gf = _ilu_gf.spec_from_file_location("gen_feeds", _gen_feeds)
            assert _spec_gf and _spec_gf.loader
            _mod_gf = _ilu_gf.module_from_spec(_spec_gf)
            _spec_gf.loader.exec_module(_mod_gf)
            _mod_gf.build_feeds(data.get("recent", []), args.output_dir,
                                swarm_user_id=fs_user_id)
            log.info("Syndication: feed.xml + feed.json written")
    except Exception as _fe:
        log.warning("gen_feeds failed: %s", _fe)

    # ── Opt-in: standalone travel-atlas journal (gen_travel_atlas.py) ──────────
    # Written last, after the PWA/placeholder passes, so the file stays a clean
    # standalone page. Fed the exact rows/stats/trips the site rendered from.
    if args.travel_atlas_out:
        try:
            from gen_tips import CTRY_NORM as _ta_ctry
            import gen_travel_atlas as _ta
            _ta_meta = _ta.write_page(args.travel_atlas_out, rows, data, trips, _photos_by_checkin,
                                      all_tips, config_dir, ctry_norm=_ta_ctry, pix_url=_pix_dir_uri)
            log.info("Travel atlas → %s (%d check-ins, %d countries, %d trips, %d photos)",
                     args.travel_atlas_out, _ta_meta["checkins"], _ta_meta["countries"],
                     _ta_meta["trips"], _ta_meta["photos"])
        except Exception as _tae:
            log.warning("gen_travel_atlas failed: %s", _tae)

    log.info("Done!")