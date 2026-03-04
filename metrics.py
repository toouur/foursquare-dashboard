"""
metrics.py  –  All aggregation and trip-detection logic.

Depends on: transform.py
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ── Date helpers ───────────────────────────────────────────────────────────────

def _parse_ts(row: dict) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(row["date"]), tz=timezone.utc)
    except (ValueError, KeyError, TypeError, OSError):
        return None


# ── Trip detection ─────────────────────────────────────────────────────────────

def detect_trips(
    rows: list[dict],
    home_city: str = "Minsk",
    min_checkins: int = 5,
) -> list[dict]:
    """
    Detect trips as consecutive non-home sequences of check-ins.

    Returns a list of trip dicts sorted chronologically, each containing:
    id, name, start_date, end_date, duration, countries, cities,
    checkin_count, unique_places, checkins, coords, unique_pts, top_cats.
    """
    valid = sorted(
        [r for r in rows if r.get("date", "").strip()],
        key=lambda r: int(r["date"]),
    )

    raw_trips: list[list[dict]] = []
    current: list[dict] = []
    for row in valid:
        if row.get("city", "").strip() != home_city:
            current.append(row)
        else:
            if current:
                raw_trips.append(current)
            current = []
    if current:
        raw_trips.append(current)

    result: list[dict] = []
    for trip_rows in raw_trips:
        if len(trip_rows) < min_checkins:
            continue

        dates = []
        for r in trip_rows:
            d = _parse_ts(r)
            if d:
                dates.append(d)
        if not dates:
            continue

        countries_c = Counter(
            r.get("country", "").strip() for r in trip_rows if r.get("country", "").strip()
        )
        cities_c = Counter(
            r.get("city", "").strip() for r in trip_rows if r.get("city", "").strip()
        )
        top_countries = [c for c, _ in countries_c.most_common()]
        top_cities    = [c for c, _ in cities_c.most_common(3)]

        if len(top_countries) == 1:
            name = f"{top_cities[0] if top_cities else top_countries[0]}, {top_countries[0]}"
        elif len(top_countries) == 2:
            name = " & ".join(top_countries[:2])
        else:
            name = f"{top_countries[0]} + {top_countries[1]} + {len(top_countries) - 2} more"

        duration = (dates[-1].date() - dates[0].date()).days + 1

        checkins: list[dict] = []
        for r in trip_rows:
            d = _parse_ts(r)
            if not d:
                continue
            try:
                lat = round(float(r["lat"]), 5)
            except (ValueError, KeyError, TypeError):
                lat = None
            try:
                lng = round(float(r["lng"]), 5)
            except (ValueError, KeyError, TypeError):
                lng = None
            checkins.append(
                {
                    "ts":       int(r["date"]),
                    "date":     d.strftime("%Y-%m-%d"),
                    "time":     d.strftime("%H:%M"),
                    "datetime": d.strftime("%d %b %Y, %H:%M"),
                    "venue":    r.get("venue", "").strip(),
                    "venue_id": r.get("venue_id", "").strip(),
                    "city":     r.get("city", "").strip(),
                    "country":  r.get("country", "").strip(),
                    "category": r.get("category", "").strip(),
                    "lat":      lat,
                    "lng":      lng,
                }
            )

        seen_v: set[str] = set()
        unique_pts: list = []
        for r in trip_rows:
            vid = r.get("venue_id", "").strip()
            if vid and vid not in seen_v:
                seen_v.add(vid)
                try:
                    unique_pts.append(
                        [round(float(r["lat"]), 5), round(float(r["lng"]), 5), r.get("venue", "").strip()]
                    )
                except (ValueError, KeyError, TypeError):
                    pass

        trip_cats = Counter(
            r.get("category", "").strip() for r in trip_rows if r.get("category", "").strip()
        )
        result.append(
            {
                "name":           name,
                "start_date":     str(dates[0].date()),
                "end_date":       str(dates[-1].date()),
                "start_ts":       int(trip_rows[0]["date"]),
                "start_year":     dates[0].year,
                "duration":       duration,
                "countries":      top_countries,
                "cities":         [c for c, _ in cities_c.most_common()],
                "checkin_count":  len(trip_rows),
                "unique_places":  len(seen_v),
                "checkins":       checkins,
                "coords":         [[c["lat"], c["lng"]] for c in checkins if c["lat"] and c["lng"]],
                "unique_pts":     unique_pts,
                "top_cats":       [[c, n] for c, n in trip_cats.most_common(10)],
            }
        )

    result.sort(key=lambda t: t["start_ts"])
    for i, t in enumerate(result):
        t["id"] = i + 1
    return result


# ── Main aggregation ───────────────────────────────────────────────────────────

def process(
    rows: list[dict],
    mappings: dict[str, Any],
    home_city: str = "Minsk",
    min_trip_checkins: int = 5,
) -> tuple[dict, list[dict]]:
    """
    Compute all dashboard metrics from pre-transformed rows.
    Returns (stats_dict, trips_list).
    """
    from transform import build_categorize_fn

    categorize     = build_categorize_fn(mappings.get("category_groups", {}))
    explorer_groups: dict[str, list[str]] = mappings.get("explorer_groups", {})

    # ── Core counters ─────────────────────────────────────────────────────────
    dates: list[datetime] = []
    for r in rows:
        d = _parse_ts(r)
        if d:
            dates.append(d)

    countries  = Counter(r["country"] for r in rows if r.get("country", "").strip())
    cities     = Counter(r["city"]    for r in rows if r.get("city", "").strip())

    # ── City → primary country mapping ───────────────────────────────────────
    city_country_ctr: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        cy = r.get("city",    "").strip()
        co = r.get("country", "").strip()
        if cy and co:
            city_country_ctr[cy][co] += 1
    city_primary_country: dict[str, str] = {
        cy: ctr.most_common(1)[0][0]
        for cy, ctr in city_country_ctr.items()
    }

    # ── Country centroids (average lat/lng per country) ───────────────────────
    _cc_coords: dict[str, list] = defaultdict(list)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        try:
            _cc_coords[co].append((float(r["lat"]), float(r["lng"])))
        except (ValueError, KeyError, TypeError):
            pass
    country_centroids: dict[str, list] = {}
    for co, pts in _cc_coords.items():
        if pts:
            country_centroids[co] = [
                round(sum(p[0] for p in pts) / len(pts), 3),
                round(sum(p[1] for p in pts) / len(pts), 3),
                len(pts),
            ]
    by_year    = Counter(d.year for d in dates)
    by_month   = Counter((d.year, d.month) for d in dates)
    by_hour    = Counter(d.hour for d in dates)
    by_dow     = Counter(d.weekday() for d in dates)

    # ── Venues: unique by venue_id with city ─────────────────────────────────
    venue_by_id: dict[str, dict] = {}
    for r in rows:
        vid  = r.get("venue_id", "").strip()
        name = r.get("venue",    "").strip()
        city = r.get("city",     "").strip()
        if not (vid and name):
            continue
        if vid not in venue_by_id:
            venue_by_id[vid] = {"name": name, "city": city, "count": 0}
        venue_by_id[vid]["count"] += 1
    venues_top500 = sorted(venue_by_id.values(), key=lambda x: -x["count"])[:500]
    venues_list   = [[v["name"], v["count"], v["city"]] for v in venues_top500]

    # ── Category groups ───────────────────────────────────────────────────────
    cat_groups: Counter = Counter()
    for r in rows:
        cat = r.get("category", "").strip()
        if cat:
            grp = categorize(cat)
            if grp:
                cat_groups[grp] += 1

    # ── Category Explorer: unique by venue_id ────────────────────────────────
    cat_vid: dict[str, dict] = defaultdict(dict)
    for r in rows:
        cat   = r.get("category", "").strip()
        vid   = r.get("venue_id", "").strip()
        venue = r.get("venue",    "").strip()
        city  = r.get("city",     "").strip()
        if not (cat and vid and venue):
            continue
        if vid not in cat_vid[cat]:
            cat_vid[cat][vid] = {"name": venue, "city": city, "count": 0}
        cat_vid[cat][vid]["count"] += 1

    explorer: dict[str, list] = {}
    for display_name, raw_cats in explorer_groups.items():
        combined: dict[str, dict] = {}
        for rc in raw_cats:
            for vid, d in cat_vid.get(rc, {}).items():
                if vid not in combined:
                    combined[vid] = {"name": d["name"], "city": d["city"], "count": 0}
                combined[vid]["count"] += d["count"]
        top50 = sorted(combined.values(), key=lambda x: -x["count"])[:50]
        if top50:
            explorer[display_name] = [[d["name"], d["city"], d["count"]] for d in top50]
    explorer_cats = [k for k in explorer_groups if k in explorer]

    # ── Unique places ─────────────────────────────────────────────────────────
    seen_ids: set[str] = set()
    seen_coords: set[tuple] = set()
    unique_places: list = []
    for r in rows:
        vid = r.get("venue_id", "").strip()
        try:
            lat, lng = round(float(r["lat"]), 5), round(float(r["lng"]), 5)
            has_coords = True
        except (ValueError, KeyError, TypeError):
            has_coords = False

        if vid:
            if vid not in seen_ids:
                seen_ids.add(vid)
                if has_coords:
                    unique_places.append([lat, lng, r.get("venue", "").strip()])
        elif has_coords:
            key = (lat, lng)
            if key not in seen_coords:
                seen_coords.add(key)
                unique_places.append([lat, lng, r.get("venue", "").strip()])

    unique_count = len(seen_ids) + len(seen_coords)

    # ── Countries by venues ───────────────────────────────────────────────────
    country_vids: dict[str, set] = defaultdict(set)
    for r in rows:
        c   = r.get("country", "").strip()
        vid = r.get("venue_id", "").strip() or r.get("venue", "").strip()
        if c and vid:
            country_vids[c].add(vid)
    countries_by_venues = sorted(
        [[c, len(v)] for c, v in country_vids.items()], key=lambda x: -x[1]
    )

    # ── All coords (kept for dot map) ────────────────────────────────────────
    all_coords: list = []
    for r in rows:
        try:
            all_coords.append([round(float(r["lat"]), 5), round(float(r["lng"]), 5)])
        except (ValueError, KeyError, TypeError):
            pass

    # ── Venues heatmap: one point per ~111m cell, weight = log(visit count) ──
    # Grouping at 3dp merges GPS micro-jitter; log dampens Minsk dominance.
    import math as _math
    _vh: dict = {}
    for r in rows:
        try:
            k = f"{float(r['lat']):.3f}|{float(r['lng']):.3f}"
            _vh[k] = _vh.get(k, 0) + 1
        except (ValueError, KeyError, TypeError):
            pass
    _vh_max = _math.log1p(max(_vh.values())) if _vh else 1.0
    venues_heatmap: list = [
        [float(k.split("|")[0]), float(k.split("|")[1]), round(_math.log1p(cnt) / _vh_max, 4)]
        for k, cnt in _vh.items()
    ]

    # ── Companions ────────────────────────────────────────────────────────────
    comp_raw: Counter = Counter()
    for r in rows:
        raw = r.get("with_name", "").strip()
        if not raw:
            continue
        for name in [n.strip() for n in raw.replace(" ,", ",").split(",")]:
            if name:
                comp_raw[name] += 1
    companions = [[n, c] for n, c in comp_raw.most_common(30)]

    # ── Day heatmap ───────────────────────────────────────────────────────────
    heatmap: dict[str, dict] = defaultdict(dict)
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        yr  = str(d.year)
        heatmap[yr][key] = heatmap[yr].get(key, 0) + 1
    heatmap = dict(sorted(heatmap.items()))

    # ── Discovery rate ────────────────────────────────────────────────────────
    _seen_disc: set[str] = set()
    _mon: dict[str, list] = defaultdict(lambda: [0, 0])
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        vid = r.get("venue_id", "").strip() or r.get("venue", "").strip()
        if not vid:
            continue
        try:
            d   = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            key = f"{d.year}-{d.month:02d}"
        except (ValueError, OSError):
            continue
        if vid not in _seen_disc:
            _seen_disc.add(vid)
            _mon[key][0] += 1
        else:
            _mon[key][1] += 1
    discovery_rate = sorted([[k, v[0], v[1]] for k, v in _mon.items()])

    # ── Venue loyalty ─────────────────────────────────────────────────────────
    _vy: dict[str, set] = defaultdict(set)
    _vi: dict[str, tuple] = {}
    _vc: Counter = Counter()
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _vy[vid].add(yr)
        _vc[vid] += 1
        if vid not in _vi:
            _vi[vid] = (r.get("venue", "").strip(), r.get("city", "").strip())
    loyal: list = []
    for vid, yrs in _vy.items():
        if len(yrs) >= 3:
            nm, cy = _vi[vid]
            loyal.append([nm, cy, sorted(yrs), _vc[vid]])
    loyal.sort(key=lambda x: (-len(x[2]), -x[3]))
    venue_loyalty = loyal[:100]

    # ── Trips ─────────────────────────────────────────────────────────────────
    trips = detect_trips(rows, home_city=home_city, min_checkins=min_trip_checkins)
    timeline = [
        {
            "id":       t["id"],
            "name":     t["name"],
            "start":    t["start_date"],
            "end":      t["end_date"],
            "days":     t["duration"],
            "countries":t["countries"][:6],
            "count":    t["checkin_count"],
            "year":     t["start_year"],
        }
        for t in trips
    ]

    # ── Recent 30 ─────────────────────────────────────────────────────────────
    valid_rows = [r for r in rows if r.get("date", "").strip()]
    recent_sorted = sorted(valid_rows, key=lambda r: int(r["date"]), reverse=True)[:30]
    recent: list[dict] = []
    for r in recent_sorted:
        d = _parse_ts(r)
        if not d:
            continue
        try:
            lat = round(float(r["lat"]), 5)
        except (ValueError, KeyError, TypeError):
            lat = None
        try:
            lng = round(float(r["lng"]), 5)
        except (ValueError, KeyError, TypeError):
            lng = None
        recent.append(
            {
                "ts":       int(r["date"]),
                "date":     d.strftime("%Y-%m-%d"),
                "time":     d.strftime("%H:%M"),
                "datetime": d.strftime("%d %b %Y, %H:%M"),
                "venue":    r.get("venue",    "").strip(),
                "venue_id": r.get("venue_id", "").strip(),
                "city":     r.get("city",     "").strip(),
                "country":  r.get("country",  "").strip(),
                "category": r.get("category", "").strip(),
                "lat":      lat,
                "lng":      lng,
            }
        )

    log.info("Cities: %d | Countries: %d | Unique places: %d | Trips: %d",
             len(cities), len(countries), unique_count, len(trips))

    if not dates:
        raise ValueError("No valid date rows found in CSV.")

    stats: dict = {
        "total":              len(rows),
        "date_min":           str(min(dates).date()),
        "date_max":           str(max(dates).date()),
        "unique_places_count":unique_count,
        "by_year":            sorted([(str(k), v) for k, v in by_year.items()]),
        "by_month":           sorted([(f"{k[0]}-{k[1]:02d}", v) for k, v in by_month.items()]),
        "by_hour":            [(k, v) for k, v in sorted(by_hour.items())],
        "by_dow":             [(k, v) for k, v in sorted(by_dow.items())],
        "countries":          [[c, n] for c, n in countries.most_common()],
        "countries_by_venues":countries_by_venues,
        "cities":             [[c, n, city_primary_country.get(c, "")] for c, n in cities.most_common()],
        "country_centroids":  country_centroids,
        "venues":             venues_list,
        "cat_groups":         cat_groups.most_common(),
        "explorer_cats":      explorer_cats,
        "explorer":           explorer,
        "unique_places":      unique_places,
        "all_coords":         all_coords,
        "venues_heatmap":     venues_heatmap,
        "companions":         companions,
        "heatmap":            heatmap,
        "discovery_rate":     discovery_rate,
        "venue_loyalty":      venue_loyalty,
        "timeline":           timeline,
        "trips_count":        len(trips),
        "recent":             recent,
    }
    return stats, trips
