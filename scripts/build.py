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

def build(data, trips, out_dir='.'):
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
    html = html.replace('{{TRIPS}}',     str(data['trips_count']))
    html = html.replace('{{STATS}}',     json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    idx_path = os.path.join(out_dir, 'index.html')
    with open(idx_path, 'w', encoding='utf-8') as f: f.write(html)
    print(f"Built ->{idx_path}  ({len(html)//1024:,} KB)")

    # ── trips.html ──────────────────────────────────────────────────────────
    trips_html = TRIPS_TEMPLATE
    trips_html = trips_html.replace('{{TRIPS_JSON}}', json.dumps(trips, ensure_ascii=False).replace('</', '<\\/'))
    trips_html = trips_html.replace('{{TOTAL_TRIPS}}', str(len(trips)))
    trips_html = trips_html.replace('{{UPDATED}}', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
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
    args = parser.parse_args()

    if not os.path.exists(args.input):
        log.error("Input file not found: %s", args.input)
        sys.exit(1)

    config_dir = Path(args.config_dir)
    settings   = load_settings(config_dir)
    trip_cfg   = settings.get("trip_detection", {})

    home_city     = args.home_city     or trip_cfg.get("home_city",    "Minsk")
    min_checkins  = args.min_checkins  or trip_cfg.get("min_checkins", 5)

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

    log.info("Computing metrics (home=%s, min_checkins=%d) …", home_city, min_checkins)
    data, trips = process(rows, mappings, home_city=home_city, min_trip_checkins=min_checkins, trip_names=trip_names, trip_exclude=trip_exclude)

    os.makedirs(args.output_dir, exist_ok=True)
    build(data, trips, out_dir=args.output_dir)

    if args.cat_list:
        save_category_list(rows, os.path.join(args.output_dir, "category_list.txt"))

    # ── Generate companion, feed, and world-cities pages ──
    _here = _SCRIPT_DIR
    for gen_script, gen_out, gen_tmpl, gen_kwargs in [
        (_here / "gen_companions.py", "companions.html",   "companions.html.tmpl",   {}),
        (_here / "gen_feed.py",       "feed.html",         "feed.html.tmpl",         {}),
        (_here / "gen_worldcities.py","world_cities.html", "world_cities.html.tmpl", {"cities_data": data.get("cities")}),
        (_here / "gen_venues.py",     "venues.html",       "venues.html.tmpl",       {}),
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

    log.info("Done!")

