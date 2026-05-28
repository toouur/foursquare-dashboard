#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_guide.py — Generate guide.html.

Single-purpose page: live, location-aware "what's nearby for me right now".
Unlike the other pages (which surface long-term aggregates), this one is
in-the-moment and answers: "I just checked in here — what should I do next?"

The Python side ships just enough data for the client to:
  • Place an anchor (last check-in OR — if user opts in — device GPS).
  • Run a session-context recommender that picks the 3 most relevant
    Foursquare categories for the user, given their taste history,
    the current local hour, day-of-week, and whether they just arrived
    in a new country.
  • Render an OSM Overpass query through a canonical FSQ→OSM tag map
    (config/categories_osm_map.json) and group results by canonical
    category buckets (config/categories.json).

All heavy normalisation (city/country) is done once here; the client only
receives normalised names so flag and badge rendering stays consistent.
"""

import argparse
import csv
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ── Recommender constants ────────────────────────────────────────────────────

# Daypart → category families.  Order matters: first-listed family wins ties
# when multiple dayparts overlap.  Categories here are canonical Foursquare
# names; the OSM tag map handles translation to OSM tags at query time.
DAYPART_CATS = {
    "morning":    ["Coffee Shop", "Café", "Bakery", "Breakfast Spot", "Pastry Shop", "Donut Shop", "Bagel Shop"],
    "lunch":      ["Restaurant", "Fast Food Restaurant", "Sandwich Spot", "Pizzeria", "Burger Joint", "Cafeteria",
                   "Kebab Restaurant", "Salad Place"],
    "afternoon":  ["Coffee Shop", "Café", "Tea Room", "Bakery", "Park", "Museum", "Art Gallery"],
    "evening":    ["Restaurant", "Pizzeria", "Italian Restaurant", "Japanese Restaurant", "Steakhouse",
                   "Bar", "Cocktail Bar", "Wine Bar", "Pub", "Gastropub"],
    "late_night": ["Bar", "Cocktail Bar", "Beer Bar", "Pub", "Nightclub", "Late-Night Eating Spot", "Lounge"],
}

# Transport / arrival hubs — used to detect "just arrived" sessions.
TRANSPORT_CATS = {
    "Airport", "International Airport", "Airport Terminal", "Airport Gate",
    "Rail Station", "Train Station", "Bus Station", "Bus Terminal",
    "Ferry Terminal", "Marine Terminal", "Port",
    "Hotel", "Hostel", "Bed and Breakfast",
}

# Categories that should be removed from any suggestion set — they're
# infrastructure / non-discoverable.  Mirrored client-side in SKIP_CATS.
SKIP_CATS = {
    "Road", "Bus Line", "Bus Stop", "Metro Station", "Rail Station",
    "Light Rail Station", "Tram Station", "Home (private)", "City",
    "Corporate Cafeteria", "Neighborhood", "Plaza", "Pedestrian Plaza",
    "Border Crossing", "Parking", "Fuel Station",
    "Country", "Region", "States and Municipalities",
}

# Categories that suit foreign-country / tourist mode.
FOREIGN_BOOST_CATS = {
    "Museum", "Art Museum", "History Museum", "Science Museum",
    "Art Gallery", "Monument", "Historic and Protected Site",
    "Castle", "Palace", "Cultural Center", "Memorial Site",
    "Scenic Lookout", "Park", "Plaza", "Cathedral", "Church",
    "Mosque", "Temple",
}


def _hour_to_daypart(h: int) -> str:
    if 6 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "lunch"
    if 14 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "late_night"


def _compute_recommendations(
    anchor_hour: int,
    user_by_hour: dict,
    liked_cats: set,
    just_arrived: bool,
    in_foreign_country: bool,
) -> list[str]:
    """Return up to 3 recommended Foursquare categories for the current
    session, blending daypart appropriateness with user history.

    Scoring:
      base       = how often the user visits this cat at this hour overall
      +50%       if liked
      +30%       if daypart-appropriate
      +100%      if just_arrived (boost food categories specifically)
      +80%       if in_foreign_country (boost tourist categories)
    """
    daypart = _hour_to_daypart(anchor_hour)
    daypart_set = set(DAYPART_CATS.get(daypart, []))
    arrival_set = {"Restaurant", "Café", "Coffee Shop", "Bar", "Bakery"}

    # Universe: any non-skip category the user has visited at this hour
    hour_counts = user_by_hour.get(anchor_hour, {})
    candidates = {c for c in hour_counts if c not in SKIP_CATS}
    # Also include daypart-appropriate cats the user has *ever* visited
    candidates |= {c for c in daypart_set if c in liked_cats or any(c in hc for hc in user_by_hour.values())}

    scored: list[tuple[float, str]] = []
    for c in candidates:
        if c in SKIP_CATS:
            continue
        base = hour_counts.get(c, 0)
        score = float(base)
        if c in liked_cats:
            score *= 1.5
            score += 5      # anchor liked cats above never-liked
        if c in daypart_set:
            score *= 1.3
        if just_arrived and c in arrival_set:
            score *= 2.0
        if in_foreign_country and c in FOREIGN_BOOST_CATS:
            score *= 1.8
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[:3]]


# ── Build ─────────────────────────────────────────────────────────────────────

def build_page(
    csv_path: str,
    config_dir: str,
    out_path: str,
    tmpl_path: str,
    rows: list | None = None,
    mappings: dict | None = None,
    **_extra,
) -> None:
    """Build guide.html.

    Args:
        csv_path: Path to checkins.csv (used to load ratings if rows not supplied)
        config_dir: Path to config directory
        out_path: Output path
        tmpl_path: Path to template
        rows: Pre-loaded check-in rows (preferred path from build.py)
        mappings: Pre-loaded mappings (city_merge, city_fixes, country_fixes)
    """
    config_dir = Path(config_dir)

    # ── Mappings ────────────────────────────────────────────────────────────
    if mappings is None:
        import yaml
        mappings = {}
        cm = config_dir / "city_merge.yaml"
        if cm.exists():
            mappings["city_merge"] = yaml.safe_load(cm.read_text(encoding="utf-8")) or {}
        cf = config_dir / "country_fixes.json"
        if cf.exists():
            mappings["country_fixes"] = json.loads(cf.read_text(encoding="utf-8"))
        cfx = config_dir / "city_fixes.json"
        if cfx.exists():
            mappings["city_fixes"] = {
                k: v for k, v in json.loads(cfx.read_text(encoding="utf-8")).items()
                if not k.startswith("_")
            }

    city_merge = mappings.get("city_merge", {})
    city_fixes = mappings.get("city_fixes", {})
    ctry_fixes = mappings.get("country_fixes", {})

    try:
        from gen_tips import CTRY_NORM as _CTRY_NORM
    except ImportError:
        _CTRY_NORM = {}

    def norm_country(raw: str) -> str:
        return _CTRY_NORM.get(raw) or ctry_fixes.get(raw, raw)

    def norm_city(raw_city: str, ts: str = "") -> str:
        if ts and ts in city_fixes:
            return city_fixes[ts]
        return city_merge.get(raw_city, raw_city)

    # ── Rows ────────────────────────────────────────────────────────────────
    if rows is None:
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        log.info("Loaded %d rows from %s", len(rows), csv_path)

    # ── Home city / country (for foreign-country detection) ────────────────
    settings_path = config_dir / "settings.yaml"
    home_city = "Minsk"
    if settings_path.exists():
        try:
            import yaml
            stg = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            home_city = stg.get("trip_detection", {}).get("home_city", "Minsk")
        except Exception:
            pass

    # Determine home_country = most-frequent country whose city matches home_city
    home_country = ""
    home_country_ctr: Counter = Counter()
    for r in rows:
        if norm_city(r.get("city", ""), r.get("date", "")) == home_city:
            home_country_ctr[norm_country(r.get("country", ""))] += 1
    if home_country_ctr:
        home_country = home_country_ctr.most_common(1)[0][0]

    # ── Build counters in a single pass ─────────────────────────────────────
    rows_sorted = sorted(rows, key=lambda x: int(x.get("date", 0) or 0), reverse=True)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_48h = now_ts - (48 * 3600)
    cutoff_30d = now_ts - (30 * 86400)

    # user_by_hour: { 0..23: { category: count } }
    user_by_hour: dict[int, Counter] = defaultdict(Counter)
    # countries seen in last 30 days (for "in_new_country" detection)
    recent_country_ctr: Counter = Counter()
    # venue meta for "have I been here" badge
    venue_meta: dict = {}
    recent_48h: list = []
    cat_counter: Counter = Counter()

    for r in rows_sorted:
        try:
            ts = int(r.get("date", 0) or 0)
        except ValueError:
            ts = 0
        cat = (r.get("category") or "").strip()
        cy_norm = norm_city(r.get("city", ""), r.get("date", ""))
        co_norm = norm_country(r.get("country", ""))
        r["_norm_city"] = cy_norm
        r["_norm_country"] = co_norm

        if cat and cat not in SKIP_CATS:
            cat_counter[cat] += 1
            if ts:
                hour_local = (ts // 3600) % 24  # UTC approximation; close enough at the daily granularity we need
                user_by_hour[hour_local][cat] += 1

        if ts >= cutoff_30d and co_norm:
            recent_country_ctr[co_norm] += 1

        if ts >= cutoff_48h:
            recent_48h.append(r)

        vid = (r.get("venue_id") or "").strip()
        if vid and vid not in venue_meta:
            venue_meta[vid] = {
                "name":    r.get("venue", ""),
                "category": cat,
                "lat":     r.get("lat", ""),
                "lng":     r.get("lng", ""),
                "city":    cy_norm,
                "country": co_norm,
            }

    # ── Anchor ──────────────────────────────────────────────────────────────
    anchor = None
    if rows_sorted:
        latest = rows_sorted[0]
        try:
            anchor_ts = int(latest.get("date", 0) or 0)
        except ValueError:
            anchor_ts = 0
        lat_raw = latest.get("lat", "")
        lng_raw = latest.get("lng", "")
        if lat_raw and lng_raw:
            anchor = {
                "venue":     latest.get("venue", ""),
                "venue_id":  latest.get("venue_id", ""),
                "category":  latest.get("category", ""),
                "city":      latest.get("_norm_city", ""),
                "country":   latest.get("_norm_country", ""),
                "lat":       float(lat_raw),
                "lng":       float(lng_raw),
                "ts":        anchor_ts,
                "iso":       (datetime.fromtimestamp(anchor_ts, tz=timezone.utc).isoformat()
                              if anchor_ts else ""),
            }

    # ── Session context ─────────────────────────────────────────────────────
    just_arrived = False
    if recent_48h:
        last_4h_cutoff = now_ts - 4 * 3600
        for r in recent_48h:
            try:
                ts = int(r.get("date", 0) or 0)
            except ValueError:
                continue
            if ts < last_4h_cutoff:
                break
            if r.get("category", "") in TRANSPORT_CATS:
                just_arrived = True
                break

    in_foreign_country = bool(anchor and home_country and anchor["country"] != home_country)
    in_new_country = bool(
        anchor and anchor["country"]
        and recent_country_ctr.get(anchor["country"], 0) <= 3
        # Only meaningful if the user has been there fewer than 3 times in last 30 days
    )

    # ── Liked categories set ────────────────────────────────────────────────
    ratings_path = Path(csv_path).resolve().parent / "venueRatings.json"
    liked_cats: set = set()
    liked_venue_ids: set = set()
    liked_venues: list = []
    if ratings_path.exists():
        try:
            ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
            for v in ratings.get("venueLikes", []):
                vid = v.get("id", "")
                if not vid:
                    continue
                liked_venue_ids.add(vid)
                meta = venue_meta.get(vid)
                if meta:
                    cat = meta.get("category", "")
                    if cat and cat not in SKIP_CATS:
                        liked_cats.add(cat)
                    if meta.get("lat") and meta.get("lng"):
                        liked_venues.append({
                            "id":       vid,
                            "name":     v.get("name", ""),
                            "category": cat,
                            "city":     meta.get("city", ""),
                            "country":  meta.get("country", ""),
                            "lat":      float(meta["lat"]),
                            "lng":      float(meta["lng"]),
                        })
        except Exception as e:
            log.warning("Failed to load ratings: %s", e)

    # ── Recommendations ─────────────────────────────────────────────────────
    anchor_hour = (anchor["ts"] // 3600) % 24 if anchor else (now_ts // 3600) % 24
    user_by_hour_dict = {h: dict(c) for h, c in user_by_hour.items()}
    recommended = _compute_recommendations(
        anchor_hour=anchor_hour,
        user_by_hour=user_by_hour_dict,
        liked_cats=liked_cats,
        just_arrived=just_arrived,
        in_foreign_country=in_foreign_country,
    )

    # ── Visited venue names in the current city (for "✓ visited" badge) ────
    visited_names: list = []
    if anchor:
        anchor_city = anchor["city"]
        seen: set = set()
        for r in rows_sorted:
            if r.get("_norm_city", "") == anchor_city:
                vn = (r.get("venue", "") or "").strip().lower()
                if vn and vn not in seen:
                    seen.add(vn)
                    visited_names.append(vn)

    # ── Categories config (canonical groups + OSM tags) ────────────────────
    cats_path = config_dir / "categories.json"
    osm_path = config_dir / "categories_osm_map.json"
    groups: dict[str, list[str]] = {}
    osm_tags: dict[str, list[str]] = {}
    if cats_path.exists():
        cdata = json.loads(cats_path.read_text(encoding="utf-8"))
        for k, v in cdata.get("category_groups", {}).items():
            if not k.startswith("_"):
                groups[k] = v
    if osm_path.exists():
        otdata = json.loads(osm_path.read_text(encoding="utf-8"))
        for k, v in otdata.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(v.get("osm"), list):
                osm_tags[k] = v["osm"]

    # ── Visited categories with count (for filter pills) ──────────────────
    visited_cats = [[c, n] for c, n in cat_counter.most_common() if c not in SKIP_CATS]

    # ── Payload ────────────────────────────────────────────────────────────
    data = {
        "anchor": anchor,
        "session": {
            "now":                  now_ts,
            "home_city":            home_city,
            "home_country":         home_country,
            "is_home":              bool(anchor and anchor["city"] == home_city),
            "in_foreign_country":   in_foreign_country,
            "in_new_country":       in_new_country,
            "just_arrived":         just_arrived,
            "checkins_last_48h":    len(recent_48h),
            "anchor_hour":          anchor_hour,
            "daypart":              _hour_to_daypart(anchor_hour),
        },
        "recommended":          recommended,
        "liked_cats":           sorted(liked_cats),
        "liked_venues":         liked_venues,
        "visited_cats":         visited_cats,           # [[cat, count], ...]
        "visited_names":        visited_names,          # lowercased venue names in current city
        "groups":               groups,                 # canonical 8-group taxonomy
        "osm_tags":             osm_tags,               # cat → [osm tags]
        # ── For the page hero (light context) ──
        "totals": {
            "total_checkins":   len(rows),
            "total_countries":  len({norm_country(r.get("country", "")) for r in rows if r.get("country")}),
            "liked_count":      len(liked_venue_ids),
        },
    }

    # ── Render ─────────────────────────────────────────────────────────────
    tmpl = Path(tmpl_path).read_text(encoding="utf-8")
    payload_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tmpl = tmpl.replace("{{GUIDE_DATA_JSON}}", payload_json)
    tmpl = tmpl.replace("{{UPDATED}}", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    tmpl = tmpl.replace("{{TOTAL_CHECKINS}}", str(data["totals"]["total_checkins"]))
    tmpl = tmpl.replace("{{TOTAL_COUNTRIES}}", str(data["totals"]["total_countries"]))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tmpl, encoding="utf-8")
    size_kb = len(tmpl) // 1024
    print(
        f"guide.html -> {out_path}  ({size_kb} KB, anchor: "
        f"{anchor['venue'] if anchor else '—'}, recommended: {recommended})"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-path", required=True)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--out-path", required=True)
    parser.add_argument("--tmpl-path", required=True)
    args = parser.parse_args()

    build_page(
        csv_path=args.csv_path,
        config_dir=args.config_dir,
        out_path=args.out_path,
        tmpl_path=args.tmpl_path,
    )
