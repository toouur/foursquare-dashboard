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
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    trips_html = trips_html.replace('{{PIX_DIR_JSON}}', pix_dir_json)
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

    log.info("Computing metrics (home=%s, min_checkins=%d) …", home_city, min_checkins)
    data, trips = process(rows, mappings, home_city=home_city, min_trip_checkins=min_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags)

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

    os.makedirs(args.output_dir, exist_ok=True)
    build(data, trips, out_dir=args.output_dir,
          pix_dir_json=json.dumps(_pix_dir_uri),
          extra_replacements={
              "{{TIPS_RECENT}}":         tips_recent_json,
              "{{TIPS_COUNT}}":          f"{tips_count:,}",
              "{{PHOTOS_KPI}}":           f'<div class="kpi"><div class="num">{total_photos:,}</div><div class="lbl">Photos</div></div>' if total_photos else '',
              "{{PHOTOS_RECENT_JSON}}":  recent_photos_json,
              "{{SWARM_USER_ID}}":       fs_user_id,
          })

    if args.cat_list:
        save_category_list(rows, os.path.join(args.output_dir, "category_list.txt"))

    # ── Generate companion, feed, world-cities, tips pages ──
    _here = _SCRIPT_DIR
    for gen_script, gen_out, gen_tmpl, gen_kwargs in [
        (_here / "gen_companions.py", "companions.html",   "companions.html.tmpl",   {}),
        (_here / "gen_feed.py",       "feed.html",         "feed.html.tmpl",         {"swarm_user_id": fs_user_id}),
        (_here / "gen_worldcities.py","world_cities.html", "world_cities.html.tmpl", {"cities_data": data.get("cities")}),
        (_here / "gen_venues.py",     "venues.html",       "venues.html.tmpl",       {}),
        (_here / "gen_tips.py",       "tips.html",         "tips.html.tmpl",         {"tips_path": str(tips_path), "pix_url": _pix_dir_uri}),
    ]:
        if gen_script.exists():
            import importlib.util as _ilu, importlib as _il
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
            except Exception as _e:
                log.warning("Generator %s failed: %s", gen_script.name, _e)
        else:
            log.warning("Generator not found: %s", gen_script)

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
                )
            except Exception as _e:
                log.warning("gen_photos.py failed: %s", _e)
        else:
            log.warning("gen_photos.py not found — skipping photos.html")

    log.info("Done!")

