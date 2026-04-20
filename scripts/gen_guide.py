#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_guide.py — Generate guide.html with personalized suggestions.

Analyzes user preferences from check-ins, ratings, categories to provide
dynamic suggestions based on recent activity.

Used by build.py — receives rows and mappings directly for proper normalization.
"""

import argparse
import csv
import json
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def build_page(csv_path: str, config_dir: str, out_path: str, tmpl_path: str, rows: list = None, mappings: dict = None):
    """Build guide.html with preference analysis.
    
    Args:
        csv_path: Path to checkins.csv (used to load ratings/venue meta if not provided)
        config_dir: Path to config directory
        out_path: Output path for guide.html
        tmpl_path: Path to template
        rows: Pre-loaded rows list (optional, will load if not provided)
        mappings: Pre-loaded mappings dict (optional, will load if not provided)
    """
    config_dir = Path(config_dir)
    
    # Load mappings if not provided (city_merge, country_fixes)
    if mappings is None:
        import yaml
        mappings = {}
        city_merge_path = config_dir / "city_merge.yaml"
        if city_merge_path.exists():
            with open(city_merge_path, encoding="utf-8") as f:
                mappings["city_merge"] = yaml.safe_load(f) or {}
        country_fixes = config_dir / "country_fixes.json"
        if country_fixes.exists():
            with open(country_fixes, encoding="utf-8") as f:
                mappings["country_fixes"] = json.load(f)
    
    city_merge = mappings.get("city_merge", {})
    ctry_norm = mappings.get("country_fixes", {})
    
    # Import CTRY_NORM for country normalization (same as other generators)
    try:
        from gen_tips import CTRY_NORM as _CTRY_NORM
    except ImportError:
        _CTRY_NORM = {}
    
    # Load rows if not provided
    if rows is None:
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        log.info("Loaded %d rows from %s", len(rows), csv_path)
    
    # Sort by date (newest first)
    rows_sorted = sorted(rows, key=lambda x: int(x.get("date", 0) or 0), reverse=True)
    
    # Get recent check-ins (last 48 hours)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    cutoff_ts = now_ts - (48 * 3600)
    recent_rows = [r for r in rows_sorted if int(r.get("date", 0) or 0) >= cutoff_ts]
    
    # Latest check-in for dynamic suggestions
    latest = recent_rows[0] if recent_rows else None
    
    # Compute user preferences from ALL check-ins (normalized)
    cat_counter = Counter()
    city_counter = Counter()
    country_counter = Counter()
    hour_counter = Counter()
    
    for r in rows:
        # Apply city normalization
        raw_city = r.get("city", "")
        city = city_merge.get(raw_city, raw_city)
        
        # Apply country normalization
        raw_country = r.get("country", "")
        country = _CTRY_NORM.get(raw_country) or ctry_norm.get(raw_country, raw_country)
        
        cat = r.get("category", "").strip()
        
        if cat:
            cat_counter[cat] += 1
        if city:
            city_counter[city] += 1
        if country:
            country_counter[country] += 1
        
        ts = int(r.get("date", 0) or 0)
        if ts:
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                hour_counter[dt.hour] += 1
            except:
                pass
    
    top_categories = cat_counter.most_common(20)
    top_cities = city_counter.most_common(15)
    top_countries = country_counter.most_common(10)
    
    # Peak hours
    peak_hours = sorted(hour_counter.items(), key=lambda x: -x[1])[:3]
    peak_hours_str = ", ".join(f"{h:02d}:00" for h, _ in peak_hours) if peak_hours else "N/A"
    
    # Load venue metadata from check-ins (first/last visit per venue)
    venue_meta = {}
    for r in rows_sorted:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        ts = int(r.get("date", 0) or 0)
        if vid not in venue_meta:
            venue_meta[vid] = {
                "name": r.get("venue", ""),
                "city": r.get("city", ""),
                "country": r.get("country", ""),
                "category": r.get("category", ""),
                "lat": r.get("lat", ""),
                "lng": r.get("lng", ""),
                "first_ts": ts,
            }
        venue_meta[vid]["last_ts"] = ts
    
    # Load ratings for liked venues
    ratings_path = Path(csv_path).resolve().parent / "venueRatings.json"
    liked_venues = []
    liked_categories = Counter()
    if ratings_path.exists():
        try:
            ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
            
            for v in ratings.get("venueLikes", []):
                vid = v.get("id", "")
                if not vid:
                    continue
                meta = venue_meta.get(vid, {})
                
                # Normalize country/city for venue
                raw_city = meta.get("city", "")
                raw_country = meta.get("country", "")
                norm_city = city_merge.get(raw_city, raw_city)
                norm_country = _CTRY_NORM.get(raw_country) or ctry_norm.get(raw_country, raw_country)
                
                liked_venues.append({
                    "id": vid,
                    "name": v.get("name", ""),
                    "category": meta.get("category", ""),
                    "city": norm_city,
                    "country": norm_country,
                    "lat": meta.get("lat", ""),
                    "lng": meta.get("lng", ""),
                })
                if meta.get("category"):
                    liked_categories[meta["category"]] += 1
        except Exception as e:
            log.warning("Failed to load ratings: %s", e)
    
    top_liked_cats = liked_categories.most_common(10)

    # All unique categories with ≥ 2 visits, sorted by frequency — for nearby pills
    all_categories = [c for c, n in cat_counter.most_common() if n >= 2]

    # Load OSM category map
    osm_cat_map = {}
    osm_map_path = config_dir / "categories_osm_map.json"
    if osm_map_path.exists():
        try:
            raw = json.loads(osm_map_path.read_text(encoding="utf-8"))
            osm_cat_map = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception as e:
            log.warning("Failed to load categories_osm_map.json: %s", e)

    # Visited venue names in the latest check-in's city (for "already visited" badge)
    visited_names_in_city: list = []
    if latest:
        raw_city = latest.get("city", "")
        latest_city_norm = city_merge.get(raw_city, raw_city)
        if latest_city_norm:
            seen_vnames: set = set()
            for r in rows:
                rc = city_merge.get(r.get("city", ""), r.get("city", ""))
                if rc == latest_city_norm:
                    vname = r.get("venue", "").strip().lower()
                    if vname and vname not in seen_vnames:
                        visited_names_in_city.append(vname)
                        seen_vnames.add(vname)

    # Build recent history (last 48 hours) with NORMALIZED city/country
    recent_history = []
    for r in recent_rows[:50]:  # Limit to 50 for performance
        ts = int(r.get("date", 0) or 0)
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            
            # Apply normalization
            raw_city = r.get("city", "")
            raw_country = r.get("country", "")
            city = city_merge.get(raw_city, raw_city)
            country = _CTRY_NORM.get(raw_country) or ctry_norm.get(raw_country, raw_country)
            
            recent_history.append({
                "ts": ts,
                "date": dt.strftime("%d %b %Y"),
                "time": dt.strftime("%H:%M"),
                "venue": r.get("venue", ""),
                "venue_id": r.get("venue_id", ""),
                "city": city,
                "country": country,
                "category": r.get("category", ""),
                "lat": r.get("lat", ""),
                "lng": r.get("lng", ""),
            })
    
    # Last 48h stats
    recent_stats = {
        "count": len(recent_rows),
        "unique_venues": len(set(r.get("venue_id", "") for r in recent_rows if r.get("venue_id"))),
        "cities": len(set(r.get("city", "") for r in recent_rows if r.get("city"))),
    }
    
    # Suggestion logic - analyze patterns
    suggestions = {
        "based_on_latest": None,
        "prefer_categories": [c[0] for c in top_liked_cats[:5]] if top_liked_cats else [c[0] for c in top_categories[:5]],
        "prefer_cities": [c[0] for c in top_cities[:5]],
        "prefer_countries": [c[0] for c in top_countries[:3]],
        "peak_hours": peak_hours_str,
    }
    
    if latest:
        # Normalize latest check-in
        raw_city = latest.get("city", "")
        raw_country = latest.get("country", "")
        norm_city = city_merge.get(raw_city, raw_city)
        norm_country = _CTRY_NORM.get(raw_country) or ctry_norm.get(raw_country, raw_country)
        
        suggestions["based_on_latest"] = {
            "venue": latest.get("venue", ""),
            "city": norm_city,
            "country": norm_country,
            "category": latest.get("category", ""),
            "lat": latest.get("lat", ""),
            "lng": latest.get("lng", ""),
        }
    
    # Compile template data
    data = {
        "total_checkins": len(rows),
        "unique_venues": len(venue_meta),
        "total_cities": len(city_counter),
        "total_countries": len(country_counter),
        "top_categories": top_categories,
        "top_cities": top_cities,
        "top_countries": top_countries,
        "liked_venues_count": len(liked_venues),
        "top_liked_cats": top_liked_cats,
        "peak_hours": peak_hours_str,
        "recent_history": recent_history,
        "recent_stats": recent_stats,
        "suggestions": suggestions,
        "latest_checkin": {
            "venue": latest.get("venue", "") if latest else "",
            "city": city_merge.get(latest.get("city", ""), latest.get("city", "")) if latest else "",
            "country": _CTRY_NORM.get(latest.get("country", ""), ctry_norm.get(latest.get("country", ""), latest.get("country", ""))) if latest else "",
            "category": latest.get("category", "") if latest else "",
            "lat": latest.get("lat", "") if latest else "",
            "lng": latest.get("lng", "") if latest else "",
            "ts": latest.get("date", "") if latest else "",
        } if latest else None,
    }
    
    # Generate map links for liked venues
    def make_maps_link(lat, lng, name, service):
        if not lat or not lng:
            return None
        if service == "google":
            return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        elif service == "yandex":
            return f"https://yandex.com/maps/?pt={lng},{lat}&z=16"
        return None
    
    venue_links = []
    for v in liked_venues[:20]:
        if v.get("lat") and v.get("lng"):
            venue_links.append({
                "name": v.get("name", ""),
                "category": v.get("category", ""),
                "city": v.get("city", ""),
                "google": make_maps_link(v["lat"], v["lng"], v["name"], "google"),
                "yandex": make_maps_link(v["lat"], v["lng"], v["name"], "yandex"),
            })
    
    data["venue_links"] = venue_links
    data["osm_cat_map"] = osm_cat_map
    data["visited_names_in_city"] = visited_names_in_city
    data["all_categories"] = all_categories
    
    # Load template and render
    tmpl = Path(tmpl_path).read_text(encoding="utf-8")
    
    # Replace placeholders
    tmpl = tmpl.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    tmpl = tmpl.replace("{{TOTAL_CHECKINS}}", str(data["total_checkins"]))
    tmpl = tmpl.replace("{{UNIQUE_VENUES}}", str(data["unique_venues"]))
    tmpl = tmpl.replace("{{TOTAL_CITIES}}", str(data["total_cities"]))
    tmpl = tmpl.replace("{{TOTAL_COUNTRIES}}", str(data["total_countries"]))
    tmpl = tmpl.replace("{{UPDATED}}", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tmpl, encoding="utf-8")
    size_kb = len(tmpl) // 1024
    print(f"guide.html -> {out_path}  ({size_kb} KB)")


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
