# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Cross-dimensional analytics + the top-level process() aggregator (metrics package)."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from transform import build_categorize_fn

from .companions import collect_companions
from .shouts import shout_analysis, shout_records
from .trips import _COUNTRY_TZ, _localise, _parse_ts, _tz_at, detect_trips

log = logging.getLogger(__name__)

# ── Cross-dimensional analytics ────────────────────────────────────────────────

def cross_dim_analysis(rows: list[dict], categorize) -> dict:
    """Hour-of-day and day-of-week breakdowns per category group, in local time."""
    # Pick top 8 category groups by overall volume so the heatmap stays legible.
    grp_ctr: Counter = Counter()
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        g = categorize(cat)
        if g:
            grp_ctr[g] += 1
    top_groups = [g for g, _ in grp_ctr.most_common(8)]
    if not top_groups:
        return {}
    g_idx = {g: i for i, g in enumerate(top_groups)}

    # hour_cat[hour][group_idx] = count;  dow_cat[dow][group_idx] = count
    hour_cat = [[0] * len(top_groups) for _ in range(24)]
    dow_cat  = [[0] * len(top_groups) for _ in range(7)]
    country_hour = defaultdict(lambda: [0] * 24)  # country → 24-hour profile

    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        g = categorize(cat)
        if g not in g_idx:
            continue
        try:
            d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
        except (ValueError, KeyError, OSError):
            continue
        try:
            lat = float(r["lat"]); lng = float(r["lng"])
        except (ValueError, KeyError, TypeError):
            lat = lng = None
        country = r.get("country", "").strip()
        d_local = _localise(d, lat, lng, country)
        h = d_local.hour
        dw = d_local.weekday()
        idx = g_idx[g]
        hour_cat[h][idx] += 1
        dow_cat[dw][idx] += 1
        if country:
            country_hour[country][h] += 1

    # Country-hour: top 12 countries by volume, normalized so each row sums to 100.
    top_cos = [c for c, _ in Counter(
        {k: sum(v) for k, v in country_hour.items()}
    ).most_common(12)]
    country_hour_pct = []
    for co in top_cos:
        row = country_hour[co]
        total = sum(row) or 1
        country_hour_pct.append([co, [round(v * 100 / total, 1) for v in row]])

    return {
        "groups":           top_groups,
        "hour_cat":         hour_cat,
        "dow_cat":          dow_cat,
        "country_hour_pct": country_hour_pct,
    }


# ── Main aggregation ───────────────────────────────────────────────────────────

def process(
    rows: list[dict],
    mappings: dict[str, Any],
    home_city: str = "Minsk",
    min_trip_checkins: int = 5,
    trip_names: dict[str, str] | None = None,
    trip_exclude: set[int] | None = None,
    trip_end_overrides: dict[int, int] | None = None,
    trip_start_overrides: dict[int, int] | None = None,
    trip_tags: dict[int, list[str]] | None = None,
    new_country_year_overrides: dict[str, int] | None = None,
    flights: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """
    Compute all dashboard metrics from pre-transformed rows.
    Returns (stats_dict, trips_list).
    """
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

    # ── City centroids (average lat/lng per city, for city dot map) ──────────
    _city_coords: dict[str, list] = defaultdict(list)
    for r in rows:
        cy = r.get("city", "").strip()
        if not cy:
            continue
        try:
            _city_coords[cy].append((float(r["lat"]), float(r["lng"])))
        except (ValueError, KeyError, TypeError):
            pass
    city_centroids: dict[str, list] = {}
    for cy, pts in _city_coords.items():
        if pts:
            city_centroids[cy] = [
                round(sum(p[0] for p in pts) / len(pts), 3),
                round(sum(p[1] for p in pts) / len(pts), 3),
            ]

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
            venue_by_id[vid] = {"name": name, "city": city, "count": 0, "id": vid}
        venue_by_id[vid]["count"] += 1
    venues_top500 = sorted(venue_by_id.values(), key=lambda x: -x["count"])[:500]
    venues_list   = [[v["name"], v["count"], v["city"], v.get("id","")] for v in venues_top500]

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
                    combined[vid] = {"name": d["name"], "city": d["city"], "count": 0, "vid": vid}
                combined[vid]["count"] += d["count"]
        top50 = sorted(combined.values(), key=lambda x: -x["count"])[:50]
        if top50:
            explorer[display_name] = [[d["name"], d["city"], d["count"], d["vid"]] for d in top50]
    explorer_cats = [k for k in explorer_groups if k in explorer]

    # ── Unique places (enriched: lat, lng, name, count, years, cat, city) ──
    # First pass: accumulate per-venue stats keyed by venue_id
    _vp: dict = {}  # venue_id → {lat, lng, name, count, last_ts, years, category, city}
    _vp_coord: dict = {}  # (lat3,lng3) → same shape, for no-id rows
    for r in rows:
        vid = r.get("venue_id", "").strip()
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
            has_coords = True
        except (ValueError, KeyError, TypeError):
            has_coords = False
        ts = int(r["date"]) if r.get("date") else 0
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else None
        raw_cat = r.get("category", "").strip()
        if vid:
            if vid not in _vp:
                _vp[vid] = {
                    "lat": lat_f if has_coords else 0.0,
                    "lng": lng_f if has_coords else 0.0,
                    "name": r.get("venue", "").strip(),
                    "count": 0, "last_ts": 0, "years": set(),
                    "cat": raw_cat,
                    "city": r.get("city", "").strip(),
                    "has_coords": has_coords,
                }
            e = _vp[vid]
            e["count"] += 1
            if ts > e["last_ts"]:
                e["last_ts"] = ts
            if yr:
                e["years"].add(yr)
            if not e["cat"] and raw_cat:
                e["cat"] = raw_cat
            if not e["city"] and r.get("city", "").strip():
                e["city"] = r.get("city", "").strip()
        elif has_coords:
            key = (round(lat_f, 3), round(lng_f, 3))
            if key not in _vp_coord:
                _vp_coord[key] = {
                    "lat": lat_f, "lng": lng_f,
                    "name": r.get("venue", "").strip(),
                    "count": 0, "last_ts": 0, "years": set(),
                    "cat": raw_cat,
                    "city": r.get("city", "").strip(),
                    "has_coords": True,
                }
            e = _vp_coord[key]
            e["count"] += 1
            if ts > e["last_ts"]:
                e["last_ts"] = ts
            if yr:
                e["years"].add(yr)

    seen_ids: set[str] = set(_vp.keys())
    seen_coords: set[tuple] = set(_vp_coord.keys())
    unique_places: list = []
    for e in _vp.values():
        if not e["has_coords"]:
            continue
        unique_places.append([
            round(e["lat"], 5), round(e["lng"], 5),
            e["name"], e["count"], sorted(e["years"]), e["cat"], e["city"],
        ])
    for e in _vp_coord.values():
        unique_places.append([
            round(e["lat"], 5), round(e["lng"], 5),
            e["name"], e["count"], sorted(e["years"]), e["cat"], e["city"],
        ])

    unique_count = len(seen_ids) + len(seen_coords)

    # ── Countries by venues ───────────────────────────────────────────────────
    country_vids: dict[str, set] = defaultdict(set)
    city_vids:    dict[str, set] = defaultdict(set)
    for r in rows:
        c   = r.get("country", "").strip()
        cy  = r.get("city",    "").strip()
        vid = r.get("venue_id", "").strip() or r.get("venue", "").strip()
        if c and vid:
            country_vids[c].add(vid)
        if cy and vid:
            city_vids[cy].add(vid)
    countries_by_venues = [
        [c, len(v)]
        for c, v in sorted(country_vids.items(), key=lambda kv: -len(kv[1]))
    ]
    cities_by_venues = [
        [cy, len(v), city_primary_country.get(cy, "")]
        for cy, v in sorted(city_vids.items(), key=lambda kv: -len(kv[1]))
    ]

    # ── Venues heatmap + per-year + per-catgroup ─────────────────────────────
    import math as _math
    # _vh: venue_id → [lat, lng, total_count, {year: count}, cat_group]
    _vh: dict = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
        except (ValueError, KeyError, TypeError):
            continue
        ts = int(r["date"]) if r.get("date") else 0
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else 0
        cg = categorize(r.get("category", "").strip()) or ""
        if vid not in _vh:
            _vh[vid] = [lat_f, lng_f, 0, {}, cg]
        _vh[vid][2] += 1
        if yr:
            _vh[vid][3][yr] = _vh[vid][3].get(yr, 0) + 1
        if not _vh[vid][4] and cg:
            _vh[vid][4] = cg

    _vh_max = _math.log1p(max(v[2] for v in _vh.values())) if _vh else 1.0

    venues_heatmap: list = [
        [v[0], v[1], round(_math.log1p(v[2]) / _vh_max, 4)]
        for v in _vh.values()
    ]

    # ── Companions ────────────────────────────────────────────────────────────
    comp_raw: Counter = Counter()
    for r in rows:
        seen_in_row: set = set()
        raw = r.get("with_name", "").strip()
        for name in [n.strip() for n in raw.replace(" ,", ",").split(",")]:
            if name:
                comp_raw[name] += 1
                seen_in_row.add(name)
        # Also count friend-created check-ins (createdBy ≠ owner), deduping against with_name
        cb = r.get("created_by_name", "").strip()
        if cb and cb not in seen_in_row:
            comp_raw[cb] += 1
            seen_in_row.add(cb)
        # Also count overlapping friends (checked in separately at same venue/time)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() != "-"]:
            if name and name not in seen_in_row:
                comp_raw[name] += 1
                seen_in_row.add(name)
    companions = [[n, c] for n, c in comp_raw.most_common(30)]

    # ── Social analytics (Group 4) ────────────────────────────────────────────
    def _has_companion(r: dict) -> bool:
        if r.get("with_name", "").strip():
            return True
        if r.get("created_by_name", "").strip():
            return True
        ov = r.get("overlaps_name", "").strip()
        return bool(ov and ov != "-")

    _social_yr: dict[int, list] = defaultdict(lambda: [0, 0])  # [solo, group]
    for r in rows:
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _social_yr[yr][1 if _has_companion(r) else 0] += 1

    solo_vs_group_by_year = sorted([[str(yr), v[0], v[1]] for yr, v in _social_yr.items()])
    _total_solo  = sum(v[0] for v in _social_yr.values())
    _total_group = sum(v[1] for v in _social_yr.values())
    solo_vs_group_totals = [_total_solo, _total_group]

    # Companions by country — top 15 companions, top 5 countries each
    _comp_cos: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        _seen: set = set()
        for name in [n.strip() for n in r.get("with_name", "").replace(" ,", ",").split(",")]:
            if name and name not in _seen:
                _comp_cos[name][co] += 1
                _seen.add(name)
        cb = r.get("created_by_name", "").strip()
        if cb and cb not in _seen:
            _comp_cos[cb][co] += 1
            _seen.add(cb)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() != "-"]:
            if name and name not in _seen:
                _comp_cos[name][co] += 1
                _seen.add(name)

    _top15 = [n for n, _ in comp_raw.most_common(15)]
    companion_countries = [
        [name, [[co, cnt] for co, cnt in _comp_cos[name].most_common()]]
        for name in _top15 if name in _comp_cos
    ]

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

    # ── Venue visit-frequency distribution ───────────────────────────────────
    # How many venues were visited exactly once, twice, …
    _vvc: Counter = Counter(_vc.values())
    _freq_buckets = [(1,'1'),(2,'2'),(3,'3'),(4,'4'),(5,'5'),(10,'6–10'),(20,'11–20'),(50,'21–50'),(999999,'50+')]
    venue_freq_dist: list = []
    _prev = 0
    for _upper, _lbl in _freq_buckets:
        _n = sum(cnt for v, cnt in _vvc.items() if _prev < v <= _upper)
        if _n:
            venue_freq_dist.append([_lbl, _n])
        _prev = _upper

    # ── Regulars: top venues by distinct calendar months visited ──────────────
    _reg_months: dict[str, set] = defaultdict(set)
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            _d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            _reg_months[vid].add((_d.year, _d.month))
        except (ValueError, OSError):
            pass
    _regulars_raw: list = []
    for vid, _months in _reg_months.items():
        if len(_months) < 3:
            continue
        _nm, _cy = _vi.get(vid, ("", ""))
        if _nm:
            _regulars_raw.append([_nm, _cy, len(_months), _vc[vid], vid])
    _regulars_raw.sort(key=lambda x: (-x[2], -x[3]))
    venue_regulars = _regulars_raw[:30]

    # ── Revisit interval histogram ────────────────────────────────────────────
    _venue_ts: dict[str, list] = defaultdict(list)
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            _venue_ts[vid].append(int(r["date"]))
        except (ValueError, TypeError):
            pass
    _iv_uppers = [7, 14, 30, 90, 180, 365, 999999]
    _iv_labels  = ['≤1 week', '1–2 weeks', '2–4 weeks', '1–3 months', '3–6 months', '6–12 months', '> 1 year']
    _iv_counts  = [0] * len(_iv_uppers)
    for _ts_list in _venue_ts.values():
        if len(_ts_list) < 2:
            continue
        for _i in range(1, len(_ts_list)):
            _days = (_ts_list[_i] - _ts_list[_i - 1]) / 86400
            for _bi, _up in enumerate(_iv_uppers):
                if _days <= _up:
                    _iv_counts[_bi] += 1
                    break
    revisit_intervals = [[_iv_labels[i], _iv_counts[i]] for i in range(len(_iv_labels))]

    # ── Distance per year (haversine between consecutive check-ins) ────────────
    import math as _math
    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
        dphi = _math.radians(lat2 - lat1)
        dlam = _math.radians(lng2 - lng1)
        a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlam / 2) ** 2
        return R * 2 * _math.asin(_math.sqrt(a))

    _coord_rows: list[tuple[int, float, float]] = []
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
            if lat_f == 0.0 and lng_f == 0.0:
                continue
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _coord_rows.append((yr, lat_f, lng_f))
        except (ValueError, KeyError, TypeError, OSError):
            pass

    _dist_yr: dict[int, float] = defaultdict(float)
    for i in range(1, len(_coord_rows)):
        yr, lat2, lng2 = _coord_rows[i]
        _, lat1, lng1 = _coord_rows[i - 1]
        try:
            d = _haversine(lat1, lng1, lat2, lng2)
            if d < 20_000:  # filter GPS teleportation artifacts
                _dist_yr[yr] += d
        except Exception:
            pass
    dist_by_year: list[list] = sorted([[str(yr), round(v)] for yr, v in _dist_yr.items()])
    total_km = round(sum(_dist_yr.values()))

    # ── Streak tracker ────────────────────────────────────────────────────────
    from datetime import timedelta as _td
    _all_dates = sorted({d.date() for d in dates})
    longest_streak = 0
    current_streak = 0
    if _all_dates:
        streak = 1
        for i in range(1, len(_all_dates)):
            if (_all_dates[i] - _all_dates[i - 1]).days == 1:
                streak += 1
            else:
                longest_streak = max(longest_streak, streak)
                streak = 1
        longest_streak = max(longest_streak, streak)
        _today = datetime.now(tz=timezone.utc).date()
        if _all_dates[-1] >= _today - _td(days=1):
            current_streak = 1
            j = len(_all_dates) - 2
            while j >= 0 and (_all_dates[j + 1] - _all_dates[j]).days == 1:
                current_streak += 1
                j -= 1

    # ── New countries by year (first visit per country) ───────────────────────
    _first_country: dict[str, int] = {}
    _first_city: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        co = r.get("country", "").strip()
        ci = r.get("city", "").strip()
        if (co and co not in _first_country) or (ci and ci not in _first_city):
            try:
                _seen_yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            except (ValueError, OSError):
                continue
            if co and co not in _first_country:
                _first_country[co] = _seen_yr
            if ci and ci not in _first_city:
                _first_city[ci] = _seen_yr
    if new_country_year_overrides:
        for co, yr in new_country_year_overrides.items():
            if co in _first_country:
                _first_country[co] = yr
    _ncby: dict[str, list] = defaultdict(list)
    for co, yr in _first_country.items():
        _ncby[str(yr)].append(co)
    new_country_by_year = sorted([[yr, sorted(cos)] for yr, cos in _ncby.items()])

    # ── Countries per year ────────────────────────────────────────────────────
    _cpy: dict[int, set] = defaultdict(set)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cpy[yr].add(co)
        except (ValueError, OSError):
            pass
    countries_per_year = sorted([[str(yr), len(cos)] for yr, cos in _cpy.items()])

    # ── Category drift (top 6 groups share by year) ───────────────────────────
    _cg_year: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        grp = categorize(cat)
        if not grp:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cg_year[yr][grp] += 1
        except (ValueError, OSError):
            pass
    _top_grps = [g for g, _ in cat_groups.most_common(7)]
    cat_drift = sorted([
        [str(yr), {g: ctr.get(g, 0) for g in _top_grps}]
        for yr, ctr in _cg_year.items()
    ])

    # ── Trips ─────────────────────────────────────────────────────────────────
    trips = detect_trips(rows, home_city=home_city, min_checkins=min_trip_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags)

    # ── Trip analytics (Group 3) ───────────────────────────────────────────────
    if trips:
        _HOME_LAT, _HOME_LNG = 53.9045, 27.5615  # Minsk

        # Duration histogram
        _dur_buckets = [1, 3, 7, 14, 28, 999]
        _dur_labels  = ['1 day', '2–3 days', '4–7 days', '1–2 weeks', '2–4 weeks', '4+ weeks']
        _dur_counts  = [0] * len(_dur_buckets)
        for _t in trips:
            _d = _t['duration']
            for _bi, _up in enumerate(_dur_buckets):
                if _d <= _up:
                    _dur_counts[_bi] += 1
                    break
        trip_duration_hist = [[_dur_labels[i], _dur_counts[i]] for i in range(len(_dur_labels))]

        # Countries per trip
        _cpt: dict[str, int] = {'1': 0, '2': 0, '3': 0, '4+': 0}
        for _t in trips:
            _n = len(_t['countries'])
            _k = str(_n) if _n <= 3 else '4+'
            _cpt[_k] = _cpt.get(_k, 0) + 1
        trip_countries_dist = [[k, _cpt[k]] for k in ['1', '2', '3', '4+']]

        # Top 10 longest trips
        _trips_by_dur = sorted(trips, key=lambda _t: -_t['duration'])[:10]
        trip_top_longest = [
            [_t['name'], _t['duration'], _t['countries'], _t['start_year']]
            for _t in _trips_by_dur
        ]

        # KPIs
        _avg_dur     = round(sum(_t['duration'] for _t in trips) / len(trips))
        _longest_t   = max(trips, key=lambda _t: _t['duration'])
        _most_cos_t  = max(trips, key=lambda _t: len(_t['countries']))
        _max_dist_km = 0.0
        _furthest: dict = {'km': 0, 'venue': '', 'city': '', 'country': ''}
        for _t in trips:
            for _ck in _t['checkins']:
                try:
                    _d = _haversine(_HOME_LAT, _HOME_LNG, float(_ck['lat']), float(_ck['lng']))
                    if _d > _max_dist_km:
                        _max_dist_km = _d
                        _furthest = {
                            'km':      round(_d),
                            'venue':   _ck['venue'],
                            'city':    _ck['city'],
                            'country': _ck['country'],
                        }
                except (TypeError, ValueError):
                    pass
        trip_kpis = {
            'avg_days':           _avg_dur,
            'longest_days':       _longest_t['duration'],
            'longest_name':       _longest_t['name'],
            'longest_year':       _longest_t['start_year'],
            'max_countries':      len(_most_cos_t['countries']),
            'max_countries_name': _most_cos_t['name'],
            'furthest_km':        _furthest['km'],
            'furthest_venue':     _furthest['venue'],
            'furthest_city':      _furthest['city'],
            'furthest_country':   _furthest['country'],
        }
    else:
        trip_duration_hist = []
        trip_countries_dist = []
        trip_top_longest = []
        trip_kpis = {}

    def _trip_km(t: dict) -> int:
        cks = sorted(t["checkins"], key=lambda c: c.get("date", 0))
        dist = 0.0
        for i in range(1, len(cks)):
            try:
                dist += _haversine(float(cks[i-1]["lat"]), float(cks[i-1]["lng"]),
                                   float(cks[i]["lat"]),   float(cks[i]["lng"]))
            except (TypeError, ValueError):
                pass
        return round(dist)

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
            "km":       _trip_km(t),
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
        # Localise the display timestamp to the check-in location's timezone.
        # Country lookup takes priority (handles DST-exempt countries like Belarus),
        # falling back to coordinate-based resolution.
        # tz_name is also passed to the Open-Meteo archive API for correct local hour.
        country_str = r.get("country", "").strip()
        tz_name = _COUNTRY_TZ.get(country_str) or _tz_at(lat, lng)
        d_local = _localise(d, lat, lng, country_str)
        recent.append(
            {
                "ts":       int(r["date"]),
                "date":     d_local.strftime("%Y-%m-%d"),
                "time":     d_local.strftime("%H:%M"),
                "datetime": d_local.strftime("%d %b %Y, %H:%M"),
                "venue":    r.get("venue",    "").strip(),
                "venue_id": r.get("venue_id", "").strip(),
                "city":     r.get("city",     "").strip(),
                "country":  r.get("country",  "").strip(),
                "category": r.get("category", "").strip(),
                "lat":        lat,
                "lng":        lng,
                "tz_name":    tz_name,
                "checkin_id": r.get("checkin_id", "").strip(),
                "companions": collect_companions(r),
                "refurbished": r.get("source_app", "").strip() == "refurbished",
            }
        )

    # ── On this day — check-ins from today's calendar day in prior years ──────
    # Localised month/day match (falls back to UTC when coords are missing), so
    # the index widget shows "N years ago today". Newest first, one card per
    # matching check-in, capped so a busy anniversary can't flood the strip.
    _today = datetime.now(tz=timezone.utc)
    _this_year = _today.year

    def _otd_entry(r: dict, d_local: datetime, *, origin: bool = False) -> dict:
        # Build one On-This-Day card. Carries the same lat/lng/tz_name/time the
        # recent cards use for their client-side Open-Meteo weather fetch, plus
        # companions, so historic cards render weather + "with …" like recent ones.
        try:
            lat = round(float(r["lat"]), 5)
        except (ValueError, KeyError, TypeError):
            lat = None
        try:
            lng = round(float(r["lng"]), 5)
        except (ValueError, KeyError, TypeError):
            lng = None
        country_str = r.get("country", "").strip()
        tz_name = _COUNTRY_TZ.get(country_str) or _tz_at(lat, lng)
        return {
            "ts":         int(r["date"]),
            "year":       d_local.year,
            "years_ago":  _this_year - d_local.year,
            "date":       d_local.strftime("%d %b %Y"),
            "time":       d_local.strftime("%H:%M"),
            "venue":      r.get("venue", "").strip(),
            "venue_id":   r.get("venue_id", "").strip(),
            "city":       r.get("city", "").strip(),
            "country":    country_str,
            "category":   r.get("category", "").strip(),
            "lat":        lat,
            "lng":        lng,
            "tz_name":    tz_name,
            "checkin_id": r.get("checkin_id", "").strip(),
            "companions": collect_companions(r),
            "refurbished": r.get("source_app", "").strip() == "refurbished",
            "origin":     origin,
        }

    on_this_day: list[dict] = []
    _earliest: "tuple[int, dict, datetime] | None" = None
    for r in valid_rows:
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
        country_str = r.get("country", "").strip()
        d_local = _localise(d, lat, lng, country_str)
        # Track the first-ever check-in so its year (the origin, e.g. 2012) can
        # anchor the widget even when it has no match for today's calendar day.
        _rts = int(r["date"])
        if _earliest is None or _rts < _earliest[0]:
            _earliest = (_rts, r, d_local)
        if (d_local.month, d_local.day) != (_today.month, _today.day):
            continue
        if d_local.year >= _this_year:
            continue
        on_this_day.append(_otd_entry(r, d_local))
    on_this_day.sort(key=lambda x: x["ts"], reverse=True)
    # Cap PER YEAR (not globally) so one very busy anniversary can't crowd out
    # older years or bloat the page — the index groups these into a year selector,
    # so every year with a match must survive. Items are newest-first, so the cap
    # keeps each year's most recent check-ins.
    _otd_per_year: dict[int, int] = {}
    _otd_capped: list[dict] = []
    for _c in on_this_day:
        _y = _c["year"]
        if _otd_per_year.get(_y, 0) >= 30:
            continue
        _otd_per_year[_y] = _otd_per_year.get(_y, 0) + 1
        _otd_capped.append(_c)
    on_this_day = _otd_capped
    # Anchor the origin year (first-ever check-in) when it isn't already a match,
    # so the widget spans the full history (e.g. 2012–now). This lone card may fall
    # on a different calendar day than "today" — accepted; it marks where it began.
    if _earliest is not None:
        _e_ts, _e_row, _e_local = _earliest
        if _e_local.year < _this_year and _e_local.year not in {c["year"] for c in on_this_day}:
            on_this_day.append(_otd_entry(_e_row, _e_local, origin=True))

    # ── Shout text mining + cross-dimensional analytics ──────────────────────
    shout_stats = shout_analysis(rows)
    cross_dim   = cross_dim_analysis(rows, categorize)

    # ── Tier 1.1 — Transport mode classification + walking + carbon ──────────
    # Speeds inferred from consecutive (ts, lat, lng) pairs.  Each segment is
    # classified into a mode; the chart shows km/year per mode + carbon est.
    # Coefficients (g CO2e per km): flight 285, car/bus 75, train 41, walk 0.
    # Boundaries below are conservative (drop 15-min gaps to avoid stop noise).
    _MODES = ["walking", "ground", "rail_likely", "flight"]
    _CO2 = {"walking": 0, "ground": 75, "rail_likely": 41, "flight": 285}
    # Reconcile the speed classifier against the real FlightRadar24 diary: a fast
    # hop only counts as a flight if it falls inside a real flight's day-window.
    # Everything unmatched (airport farewells, overnight trains checked in the
    # next morning, GPS jumps) is NOT a flight — demote it to ground.
    _flight_windows: list[tuple[int, int]] = []
    _real_flights_by_year: Counter = Counter()
    for _f in (flights or []):
        _fd = (_f.get("date") or "").strip()
        if not _fd:
            continue
        try:
            _fy = int(_fd[:4])
            _fd0 = datetime.strptime(_fd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
        _real_flights_by_year[_fy] += 1
        _fts0 = int(_fd0.timestamp())
        _slack = 3 * 3600
        _flight_windows.append((_fts0 - _slack, _fts0 + 86400 + _slack))
    _flight_windows.sort()

    def _in_flight_window(ts_a: int, ts_b: int) -> bool:
        for _w0, _w1 in _flight_windows:
            if _w0 > ts_b:
                break
            if ts_a <= _w1 and ts_b >= _w0:
                return True
        return False

    _seg_rows = []
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        try:
            t = int(r["date"]); la = float(r["lat"]); lo = float(r["lng"])
            if la == 0 and lo == 0:
                continue
            _seg_rows.append((t, la, lo))
        except (ValueError, KeyError, TypeError):
            pass
    _mode_km_by_year: dict[int, dict[str, float]] = defaultdict(lambda: {m: 0.0 for m in _MODES})
    _co2_by_year: dict[int, float] = defaultdict(float)
    _flight_legs = []   # [year, from_lat, from_lng, to_lat, to_lng, km]
    _walk_km_total = 0.0
    for i in range(1, len(_seg_rows)):
        t0, la0, lo0 = _seg_rows[i - 1]
        t1, la1, lo1 = _seg_rows[i]
        dt = t1 - t0
        if dt <= 0 or dt > 86400:           # cap to 24h between pts
            continue
        try:
            d_km = _haversine(la0, lo0, la1, lo1)
        except Exception:
            continue
        if d_km <= 0 or d_km > 20000:
            continue
        # km/h
        speed = d_km / (dt / 3600.0)
        # mode buckets (defensive — middle band stays "ground")
        if speed > 200:
            mode = "flight"
        elif 80 < speed <= 200 and d_km > 50:
            mode = "rail_likely"
        elif speed <= 6 and d_km < 12:
            mode = "walking"
        else:
            mode = "ground"
        # A "flight" that matches no real FR24 flight window is a false positive
        # (GPS jump, transit hub farewell, overnight train). Only demote when we
        # actually have a diary to reconcile against — otherwise keep legacy
        # behaviour so a missing flights.csv doesn't erase the flight layer.
        if mode == "flight" and _flight_windows and not _in_flight_window(t0, t1):
            mode = "rail_likely" if d_km > 200 else "ground"
        yr = datetime.fromtimestamp(t1, tz=timezone.utc).year
        _mode_km_by_year[yr][mode] += d_km
        _co2_by_year[yr] += d_km * _CO2[mode]
        if mode == "flight":
            _flight_legs.append([yr, round(la0, 3), round(lo0, 3),
                                  round(la1, 3), round(lo1, 3), round(d_km)])
        elif mode == "walking":
            _walk_km_total += d_km
    transport_modes = sorted([
        [str(yr), {m: round(_mode_km_by_year[yr][m]) for m in _MODES}]
        for yr in _mode_km_by_year
    ])
    transport_co2 = sorted([[str(yr), round(_co2_by_year[yr] / 1000)] for yr in _co2_by_year])
    transport_kpis = {
        "flight_legs":     len(_flight_legs),
        "walk_km_total":   round(_walk_km_total),
        "co2_tonnes":      round(sum(_co2_by_year.values()) / 1_000_000, 1),
    }
    # Limit flight legs payload to the longest 100 for the map overlay
    flight_legs = sorted(_flight_legs, key=lambda x: -x[5])[:100]

    # ── Tier 1.2 — Cohort venue retention ────────────────────────────────────
    # For each venue, get the year of FIRST visit (cohort) and every year visited.
    # cohort_retention[i][j] = % of cohort-i venues revisited in year-i+j.
    _venue_years: dict[str, set] = defaultdict(set)
    _venue_first: dict[str, int] = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _venue_years[vid].add(yr)
        if vid not in _venue_first or yr < _venue_first[vid]:
            _venue_first[vid] = yr
    _all_years = sorted(set(_venue_first.values()))
    _cohort_size: dict[int, int] = Counter(_venue_first.values())
    cohort_retention = []
    for cohort_yr in _all_years:
        cohort_vids = [v for v, y in _venue_first.items() if y == cohort_yr]
        size = len(cohort_vids)
        _ret_by_year: list[int | None] = []
        for yr in _all_years:
            if yr < cohort_yr:
                _ret_by_year.append(None)
            else:
                hits = sum(1 for v in cohort_vids if yr in _venue_years[v])
                _ret_by_year.append(round(hits * 100 / size) if size else 0)
        cohort_retention.append({"cohort": cohort_yr, "size": size, "by_year": _ret_by_year})

    # ── Tier 1.3 — Distance from home + nomad score ──────────────────────────
    # Per-day mean distance from home centroid + rolling 30-day mean for chart.
    _HOME_LAT, _HOME_LNG = country_centroids.get(home_city, [53.9045, 27.5615, 0])[:2] \
        if home_city in country_centroids else (53.9045, 27.5615)
    # Try to use the actual home_city centroid first (more accurate than country)
    if home_city in city_centroids:
        _HOME_LAT, _HOME_LNG = city_centroids[home_city]
    _daily_dist: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        try:
            t = int(r["date"]); la = float(r["lat"]); lo = float(r["lng"])
            if la == 0 and lo == 0:
                continue
            d = _haversine(_HOME_LAT, _HOME_LNG, la, lo)
            key = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
            _daily_dist[key].append(d)
        except (ValueError, KeyError, TypeError, OSError):
            pass
    # Sample: one mean per day, sorted
    distance_from_home: list[list] = []
    for k in sorted(_daily_dist.keys()):
        m = sum(_daily_dist[k]) / len(_daily_dist[k])
        distance_from_home.append([k, round(m)])
    # Nomad score: % of days where mean > 50km
    _nomad_days = sum(1 for k, m in distance_from_home if m > 50)
    nomad_kpis = {
        "nomad_days":    _nomad_days,
        "total_days":    len(distance_from_home),
        "nomad_pct":     round(_nomad_days * 100 / len(distance_from_home), 1) if distance_from_home else 0,
        "max_dist_km":   round(max((m for _, m in distance_from_home), default=0)),
    }

    # ── Tier 1.4 — Daily extremes (first / last check-in of day drift) ───────
    # Per day in LOCAL time, find earliest and latest hour (decimal).
    # Then aggregate per year (median first/last) so the chart shows long-term drift.
    _day_extremes: dict[str, dict] = defaultdict(lambda: {"first": 24, "last": 0})
    for r in rows:
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        try:
            la = float(r["lat"]); lo = float(r["lng"])
        except (ValueError, KeyError, TypeError):
            la = lo = None
        d_utc = datetime.fromtimestamp(t, tz=timezone.utc)
        d_loc = _localise(d_utc, la, lo, r.get("country", "").strip())
        key = d_loc.strftime("%Y-%m-%d")
        h = d_loc.hour + d_loc.minute / 60.0
        if h < _day_extremes[key]["first"]:
            _day_extremes[key]["first"] = h
        if h > _day_extremes[key]["last"]:
            _day_extremes[key]["last"] = h
    # Aggregate to median per year
    _ext_by_year: dict[int, dict] = defaultdict(lambda: {"first": [], "last": []})
    for key, ext in _day_extremes.items():
        yr = int(key[:4])
        _ext_by_year[yr]["first"].append(ext["first"])
        _ext_by_year[yr]["last"].append(ext["last"])
    def _med(xs):
        if not xs:
            return None
        xs2 = sorted(xs)
        n = len(xs2)
        return xs2[n // 2] if n % 2 else (xs2[n // 2 - 1] + xs2[n // 2]) / 2
    daily_extremes = sorted([
        [str(yr),
         round(_med(_ext_by_year[yr]["first"]), 2),
         round(_med(_ext_by_year[yr]["last"]),  2)]
        for yr in _ext_by_year
    ])

    # ── Tier 2.1 — Companion lifecycle (first / last seen, gap, active span) ─
    # For each top-15 companion compute: first_ts, last_ts, active_days, n_meetings,
    # median gap (days), most-common city, top 3 venues.
    _comp_ts: dict[str, list] = defaultdict(list)        # name -> [ts, ...]
    _comp_venues: dict[str, Counter] = defaultdict(Counter)
    _comp_cities: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        names = set()
        for name in [n.strip() for n in r.get("with_name", "").replace(" ,", ",").split(",")]:
            if name:
                names.add(name)
        cb = r.get("created_by_name", "").strip()
        if cb:
            names.add(cb)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() and n.strip() != "-"]:
            names.add(name)
        venue = r.get("venue", "").strip()
        city  = r.get("city",  "").strip()
        for n in names:
            _comp_ts[n].append(t)
            if venue:
                _comp_venues[n][venue] += 1
            if city:
                _comp_cities[n][city] += 1
    companion_lifecycle = []
    for name, _cnt in comp_raw.most_common(20):
        ts_list = sorted(_comp_ts[name])
        if not ts_list:
            continue
        first_ts = ts_list[0]
        last_ts  = ts_list[-1]
        gaps = [(ts_list[i] - ts_list[i - 1]) / 86400 for i in range(1, len(ts_list))]
        med_gap = _med(gaps) if gaps else 0
        top_v = _comp_venues[name].most_common(1)
        top_c = _comp_cities[name].most_common(1)
        companion_lifecycle.append({
            "name":      name,
            "n":         len(ts_list),
            "first_ts":  first_ts,
            "last_ts":   last_ts,
            "active_days": round((last_ts - first_ts) / 86400),
            "med_gap":   round(med_gap, 1) if med_gap else 0,
            "top_venue": top_v[0][0] if top_v else "",
            "top_city":  top_c[0][0] if top_c else "",
        })

    # ── Tier 2.3 — City graduation funnel ────────────────────────────────────
    _funnel_thresholds = [1, 5, 10, 25, 50, 100, 250]
    _city_visit_counts = cities  # already a Counter
    _funnel_counts = []
    for t in _funnel_thresholds:
        _funnel_counts.append(sum(1 for _, n in _city_visit_counts.items() if n >= t))
    city_funnel = [[t, n] for t, n in zip(_funnel_thresholds, _funnel_counts)]

    # ── Tier 2.4 — Activity decay per category (absolute counts per year) ────
    _cat_abs: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        grp = categorize(cat)
        if not grp:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cat_abs[grp][yr] += 1
        except (ValueError, OSError):
            pass
    _years_sorted = sorted({yr for yrs in _cat_abs.values() for yr in yrs})
    cat_trajectory_abs = []
    for grp in _top_grps:
        series = [_cat_abs[grp].get(yr, 0) for yr in _years_sorted]
        cat_trajectory_abs.append({"group": grp, "series": series})
    cat_trajectory_years = [str(yr) for yr in _years_sorted]

    # ── Tier 5.2 — source_app trends ─────────────────────────────────────────
    _app_by_year: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        app = (r.get("source_app", "") or "").strip()
        if not app:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _app_by_year[yr][app] += 1
        except (ValueError, OSError):
            pass
    _all_apps = set()
    for c in _app_by_year.values():
        _all_apps |= set(c.keys())
    _top_apps = sorted(_all_apps,
                       key=lambda a: -sum(_app_by_year[y].get(a, 0) for y in _app_by_year))[:6]
    source_app_by_year = sorted([
        [str(yr), {a: _app_by_year[yr].get(a, 0) for a in _top_apps}]
        for yr in _app_by_year
    ])

    # ── Tier 6.4 — Diversity index (Shannon entropy of category mix / year) ─
    diversity_by_year = []
    import math as _math2
    for yr, ctr in _cg_year.items():
        total = sum(ctr.values())
        if not total:
            continue
        h = 0.0
        for v in ctr.values():
            p = v / total
            if p > 0:
                h -= p * _math2.log2(p)
        diversity_by_year.append([str(yr), round(h, 2)])
    diversity_by_year.sort()

    # ── Tier 6.3 — Dormant venues ("what you miss") ──────────────────────────
    # Venues with >=10 historical visits but no visit in the last 365 days,
    # surfaced as top 30 by total visits.
    _365_ago = (int(datetime.now(tz=timezone.utc).timestamp())) - 365 * 86400
    _v_last: dict[str, int] = {}
    _v_total: Counter = Counter()
    _v_info: dict[str, tuple] = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        if t > _v_last.get(vid, 0):
            _v_last[vid] = t
        _v_total[vid] += 1
        if vid not in _v_info:
            _v_info[vid] = (r.get("venue", "").strip(), r.get("city", "").strip())
    dormant_venues = []
    for vid, last in _v_last.items():
        if _v_total[vid] >= 10 and last < _365_ago:
            nm, cy = _v_info[vid]
            dormant_venues.append({
                "name":    nm,
                "city":    cy,
                "visits":  _v_total[vid],
                "last_ts": last,
                "days_ago": round((int(datetime.now(tz=timezone.utc).timestamp()) - last) / 86400),
            })
    dormant_venues.sort(key=lambda x: -x["visits"])
    dormant_venues = dormant_venues[:30]

    # ── Tier 6.1 — Year-in-review with vivid narrative ──────────────────────
    # For each year compute a small set of headline facts and a short narrative.
    # Each year_summary also feeds the per-year album page (gen_year_pages.py).
    _months_full = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"]
    _MONTH_VIBE = {
        "January":   "deep-winter",
        "February":  "snow-bound",
        "March":     "thawing",
        "April":     "spring-edged",
        "May":       "blooming",
        "June":      "long-day",
        "July":      "midsummer",
        "August":    "late-summer",
        "September": "amber",
        "October":   "autumnal",
        "November":  "grey-skied",
        "December":  "year-closing",
    }
    # Compute year totals first so we can compare each year to the personal record
    _yr_totals = {yr: 0 for yr in {d.year for d in dates}}
    _yr_rows: dict[int, list] = defaultdict(list)
    for r in rows:
        try:
            t = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if not t:
            continue
        try:
            yr = datetime.fromtimestamp(t, tz=timezone.utc).year
        except OSError:
            continue
        _yr_rows[yr].append(r)
        _yr_totals[yr] = _yr_totals.get(yr, 0) + 1
    _max_yr_total = max(_yr_totals.values()) if _yr_totals else 0
    _mean_yr_total = sum(_yr_totals.values()) / len(_yr_totals) if _yr_totals else 0

    # ── First-person memoir phrasing pools ─────────────────────────────────
    # Each pool is picked deterministically by year so adjacent years feel
    # different but every rebuild reads the same.  Voice: a warm travel diary
    # in the first person — reflective, grounded, emotion through understatement.
    # The sentences name places, people and habits rather than dump counts.
    _BUSY_INTROS = [
        "This was the year I lived widest, further than any before it",
        "I have never filled a year quite this full",
        "The year I barely paused for breath",
        "My busiest year on record — and I felt every mile of it",
    ]
    _TRAVEL_INTROS = [
        "A year of long horizons, and I followed most of them",
        "I spent this one chasing the far edge of the map",
        "The year the departures outnumbered the quiet weeks",
        "I kept turning the map over, month after month",
    ]
    _ROAM_INTROS = [
        "I scattered myself across the map this year, flag after flag",
        "A wandering year — I woke up in a different country more often than not",
        "The year my passport did most of the talking",
        "I drifted country to country and rarely looked back",
    ]
    _HOME_INTROS = [
        "I stayed closer to home this year, and I noticed more for it",
        "The year I let familiar streets deepen instead of chasing new ones",
        "A grounded year — I planted myself and paid attention",
        "Less distance this time, but more of what was already around me",
    ]
    _QUIET_INTROS = [
        "A quieter year, and I didn't mind the stillness",
        "I moved less this year and lingered more",
        "The year of second visits and slow streets",
        "A softer chapter — fewer stops, longer pauses between them",
    ]
    _DEFAULT_INTROS = [
        "A steady year, and I walked it at my own pace",
        "The year found its rhythm and I kept to it",
        "A balance of familiar ground and a few new turnings",
        "I moved through this year without hurry",
    ]
    _ANCHOR_PHRASES = [
        "always circling back to <strong>{city}</strong>",
        "with <strong>{city}</strong> the place I kept returning to",
        "anchored, as ever, in <strong>{city}</strong>",
        "with <strong>{city}</strong> holding the centre of it",
        "orbiting <strong>{city}</strong> between everything else",
    ]
    _PEAK_PHRASES = [
        "and it was {vibe}<strong>{mon}</strong> that burned brightest",
        "with {vibe}<strong>{mon}</strong> the fullest weeks of all",
        "peaking somewhere in {vibe}<strong>{mon}</strong>",
        "and {vibe}<strong>{mon}</strong> ran fastest of the twelve",
    ]
    _NEW_PHRASES = [
        "I added <strong>{n}</strong> first-time countries",
        "<strong>{n}</strong> new countries opened up to me",
        "<strong>{n}</strong> fresh flags went onto the map",
        "<strong>{n}</strong> new countries entered the album",
    ]
    _ANCHOR_VENUE = [
        "and <strong>{v}</strong> quietly became a daily ritual",
        "and <strong>{v}</strong> turned into my steady fixture",
        "and I found myself at <strong>{v}</strong> again and again",
    ]
    _COMPANION_PHRASES = [
        "and I shared most of it with <strong>{c}</strong>",
        "with <strong>{c}</strong> beside me through much of it",
        "more often than not, <strong>{c}</strong> was there too",
    ]
    _FIRST_COUNTRY_PHRASES = [
        "I set foot in <strong>{c}</strong> for the first time",
        "<strong>{c}</strong> was new ground for me",
        "<strong>{c}</strong> joined the map for the first time",
    ]
    _JOURNEY_ONE = [
        "One trip broke the pattern — <strong>{name}</strong>, setting out that {mon}",
        "I made one real escape: <strong>{name}</strong>, come {mon}",
        "The single departure was <strong>{name}</strong>, back in {mon}",
    ]
    _JOURNEY_FEW = [
        "{n} journeys shaped the year — {list}",
        "I took to the road {n} times: {list}",
        "{n} trips marked the calendar — {list}",
    ]
    _JOURNEY_MANY = [
        "I travelled {n} times over, opening with <strong>{first}</strong> in {fm} and closing with <strong>{last}</strong> in {lm}",
        "{n} journeys in all, from <strong>{first}</strong> in {fm} to <strong>{last}</strong> in {lm}",
        "the suitcase barely rested — {n} departures, from <strong>{first}</strong> ({fm}) to <strong>{last}</strong> ({lm})",
    ]
    _FLIGHT_PHRASES = [
        "I took to the air <strong>{n}</strong> times",
        "<strong>{n}</strong> flights stitched the distances together",
        "<strong>{n}</strong> times the ground fell away from a window",
    ]
    _TRANSPORT_FOOT = [
        "though most of it I covered on foot",
        "though I walked far more of it than I rode",
        "much of it measured out step by step",
    ]
    _TRANSPORT_RAIL = [
        "a good part of it spent watching countries pass from a train window",
        "with much of the moving done by rail",
        "the long hauls mostly by train",
    ]
    _TRANSPORT_BIKE = [
        "a good stretch of it done from the saddle",
        "with the bicycle doing more of the work than usual",
        "much of the distance pedalled",
    ]
    _FAR_PHRASES = [
        "reaching as far as <strong>{city}</strong>, {km} km from home",
        "at the farthest I stood <strong>{km} km</strong> out, in <strong>{city}</strong>",
        "the furthest I got was <strong>{city}</strong>, {km} km from home",
    ]
    _DIST_PHRASES = [
        "Some <strong>{km} km</strong> passed under me between check-ins",
        "I covered <strong>{km} km</strong> of ground that year",
        "It came to <strong>{km} km</strong> on the move",
    ]
    _NEW_CITY_PHRASES = [
        "<strong>{n}</strong> cities I'd never seen before — {sample} among them",
        "<strong>{n}</strong> first-time cities, {sample} included",
        "I met <strong>{n}</strong> new cities, {sample} among them",
    ]
    _ACTIVITY_PHRASES = [
        (("coffee", "café", "cafe", "tea"),
         ["the coffee-shop habit held firm", "I kept the café ritual going", "most mornings started over coffee"]),
        (("trail", "hiking", "mountain", "park", "scenic", "forest", "lake", "national park"),
         ["a lot of it spent outdoors, on trails and lookouts", "I went looking for the green edges of things",
          "the outdoors pulled me out more than usual"]),
        (("bar", "pub", "brewery", "cocktail", "wine", "beer", "nightclub"),
         ["with more than a few evenings out", "the evenings ran late more than once",
          "a fair share of nights out folded in"]),
        (("restaurant", "diner", "bistro", "food", "bbq", "steak", "pizza", "sushi", "ramen"),
         ["so much of it happened over a table", "I did a lot of my living at the dinner table",
          "a good deal of it measured in shared meals"]),
        (("gym", "fitness", "yoga", "climbing", "pool", "stadium", "sports"),
         ["and I kept showing up to train", "with the gym a steady part of the week",
          "I kept the body moving through it"]),
        (("museum", "gallery", "art", "history", "theater", "theatre", "concert", "music"),
         ["I wandered a lot of museums and galleries", "the culture pulled me in — galleries, stages, quiet rooms",
          "much of it spent in front of art"]),
        (("beach", "resort", "island", "harbor", "harbour", "pier"),
         ["with sand underfoot more than once", "the coast kept calling me back",
          "a season of it spent near the water"]),
    ]
    _SHOUT_FRAME = [
        "Somewhere in it I wrote: <em>“{q}”</em>",
        "A line I left on a check-in that year: <em>“{q}”</em>",
        "One note I left behind: <em>“{q}”</em>",
    ]
    _OPEN_CLOSE = [
        "I opened the year at <strong>{fv}</strong>{fc} and signed it off in {lm} at <strong>{lv}</strong>{lc}",
        "It began at <strong>{fv}</strong>{fc} in {fm} and ended at <strong>{lv}</strong>{lc}",
        "First stop <strong>{fv}</strong>{fc} in {fm}; the last was <strong>{lv}</strong>{lc}",
    ]

    def _pick(pool: list[str], yr: int, salt: int = 0) -> str:
        """Deterministic pick — same year reads the same on every build."""
        return pool[(yr + salt) % len(pool)]

    def _cap_html(s: str) -> str:
        """Capitalize the first plain letter, skipping any HTML opener."""
        i = 0
        while i < len(s) and s[i] == "<":
            close = s.find(">", i)
            if close < 0:
                break
            i = close + 1
        return s[:i] + s[i].upper() + s[i + 1:] if i < len(s) else s

    def _activity_clause(top_cat: str, yr: int) -> str:
        """Map the year's dominant category to a first-person activity line."""
        low = (top_cat or "").lower()
        for keys, pool in _ACTIVITY_PHRASES:
            if any(k in low for k in keys):
                return _pick(pool, yr, 10)
        return ""

    def _transport_clause(yr: int, mode_km: dict | None, bicycle: bool) -> str:
        """Pick a transport-texture clause from the year's per-mode km."""
        if bicycle:
            return _pick(_TRANSPORT_BIKE, yr, 11)
        if not mode_km:
            return ""
        walk = mode_km.get("walking", 0.0)
        ground = mode_km.get("ground", 0.0)
        rail = mode_km.get("rail_likely", 0.0)
        land = walk + ground + rail
        if land <= 0:
            return ""
        if rail > 0.25 * land and rail > 300:
            return _pick(_TRANSPORT_RAIL, yr, 11)
        if walk > 0.55 * (walk + ground) and walk > 80:
            return _pick(_TRANSPORT_FOOT, yr, 11)
        return ""

    def _vivid(yr: int, total: int, peak_mon_name: str, peak_mon_n: int,
               top_city: str, top_city_n: int, top_cat: str, top_cat_n: int,
               top_venue: str, top_venue_n: int, n_new_countries: int,
               n_cities: int, n_countries: int, top_companion: str = "",
               first_new_country: str = "", trips_y: list | None = None,
               distance_km: int = 0, n_new_cities: int = 0,
               new_city_sample: list[str] | None = None,
               first_stop: tuple[str, str, str] = ("", "", ""),
               last_stop: tuple[str, str, str] = ("", "", ""),
               farthest_city: str = "", farthest_km: int = 0,
               n_flights: int = 0, mode_km: dict | None = None,
               bicycle: bool = False, shout_quote: str = "") -> str:
        """Compose a first-person "warm memoir" year storyline (3-6 sentences).

        S1 — the year's character + place anchor + temporal peak.
        S2 — movement: trips, flights, transport texture, farthest reach.
        S3 — discovery: first-time countries and cities.
        S4 — the people I shared it with + the hobby/activity texture.
        S5 — the end-to-end arc (first and last check-in of the year).
        S6 — a line I actually left on a check-in that year (my own voice).
        Every phrase is picked deterministically by year so each rebuild
        reads the same, but adjacent years read differently.
        """
        # Pick the intro by year character
        if total >= 0.95 * _max_yr_total:
            intro = _pick(_BUSY_INTROS, yr)
        elif total >= 0.7 * _max_yr_total:
            intro = _pick(_TRAVEL_INTROS, yr)
        elif n_countries >= 15:
            intro = _pick(_ROAM_INTROS, yr)
        elif n_countries <= 3 and total > 0.4 * _max_yr_total:
            intro = _pick(_HOME_INTROS, yr)
        elif total < 0.3 * _mean_yr_total:
            intro = _pick(_QUIET_INTROS, yr)
        else:
            intro = _pick(_DEFAULT_INTROS, yr)

        # ── S1 — character + anchor + peak ─────────────────────────────
        s1_parts: list[str] = [intro]
        if top_city and top_city_n and top_city_n > max(40, total * 0.10):
            s1_parts.append(_pick(_ANCHOR_PHRASES, yr, 1).format(city=top_city))
        if peak_mon_name:
            vibe = _MONTH_VIBE.get(peak_mon_name, "")
            vibe_str = (vibe + " ") if vibe else ""
            s1_parts.append(_pick(_PEAK_PHRASES, yr, 2).format(vibe=vibe_str, mon=peak_mon_name))
        sentence1 = ", ".join(s1_parts).rstrip(",") + "."

        def _trip_mon(t: dict) -> str:
            try:
                return _months_full[int(str(t.get("start_date", ""))[5:7]) - 1]
            except (ValueError, IndexError):
                return ""

        # ── S2 — movement: journeys, flights, farthest, transport ──────
        s2_bits: list[str] = []
        tl = trips_y or []
        if len(tl) == 1:
            s2_bits.append(_pick(_JOURNEY_ONE, yr, 6).format(
                name=tl[0].get("name") or "a trip", mon=_trip_mon(tl[0])))
        elif 2 <= len(tl) <= 3:
            listed = ", ".join(
                f"<strong>{t.get('name') or 'a trip'}</strong> in {_trip_mon(t)}"
                for t in tl)
            s2_bits.append(_pick(_JOURNEY_FEW, yr, 6).format(n=len(tl), list=listed))
        elif len(tl) >= 4:
            s2_bits.append(_pick(_JOURNEY_MANY, yr, 6).format(
                n=len(tl),
                first=tl[0].get("name") or "a trip", fm=_trip_mon(tl[0]),
                last=tl[-1].get("name") or "a trip", lm=_trip_mon(tl[-1])))
        if n_flights >= 3:
            s2_bits.append(_pick(_FLIGHT_PHRASES, yr, 12).format(n=n_flights))
        if farthest_city and farthest_km >= 500 and farthest_city != top_city:
            s2_bits.append(_pick(_FAR_PHRASES, yr, 7).format(
                city=farthest_city, km=f"{farthest_km:,}"))
        elif not s2_bits and distance_km >= 2000:
            s2_bits.append(_pick(_DIST_PHRASES, yr, 7).format(km=f"{distance_km:,}"))
        transport = _transport_clause(yr, mode_km, bicycle)
        if transport and s2_bits:
            s2_bits.append(transport)
        sentence2 = _cap_html(", ".join(s2_bits[:3])) + "." if s2_bits else ""

        # ── S3 — discovery (first-time countries and cities) ───────────
        s3_bits: list[str] = []
        if n_new_countries:
            if n_new_countries <= 2 and first_new_country:
                s3_bits.append(_pick(_FIRST_COUNTRY_PHRASES, yr, 3).format(c=first_new_country))
            else:
                s3_bits.append(_pick(_NEW_PHRASES, yr, 3).format(n=n_new_countries))
        if n_new_cities >= 4 and new_city_sample:
            sample = " and ".join(f"<strong>{c}</strong>" for c in new_city_sample[:2])
            s3_bits.append(_pick(_NEW_CITY_PHRASES, yr, 8).format(n=n_new_cities, sample=sample))
        sentence3 = _cap_html(", ".join(s3_bits[:2])) + "." if s3_bits else ""

        # ── S4 — the people + the hobby/activity texture ───────────────
        s4_bits: list[str] = []
        if top_companion:
            s4_bits.append(_pick(_COMPANION_PHRASES, yr, 5).format(c=top_companion))
        activity = _activity_clause(top_cat, yr)
        if activity:
            s4_bits.append(activity)
        joined4 = ", ".join(s4_bits[:2])
        if joined4.startswith("and "):
            joined4 = joined4[4:]
        sentence4 = _cap_html(joined4) + "." if joined4 else ""

        # ── S5 — end-to-end arc (first + last check-in of the year) ────
        fv, fc, fm = first_stop
        lv, lc, lm = last_stop
        sentence5 = ""
        if fv and lv and fv != lv:
            fc_str = f" in {fc}" if fc and fc != top_city else ""
            lc_str = f" in {lc}" if lc and lc != top_city else ""
            sentence5 = _pick(_OPEN_CLOSE, yr, 9).format(
                fv=fv, fc=fc_str, fm=fm, lv=lv, lc=lc_str, lm=lm) + "."

        # ── S6 — a line in my own words (a real shout that year) ───────
        sentence6 = ""
        if shout_quote:
            sentence6 = _pick(_SHOUT_FRAME, yr, 13).format(q=shout_quote)

        return " ".join(
            s for s in [sentence1, sentence2, sentence3,
                        sentence4, sentence5, sentence6] if s
        ).strip()

    # Per-year flight counts for the narrative. Prefer the real FlightRadar24
    # diary (authoritative); fall back to the reconciled classifier legs only
    # when no flights.csv is present.
    _flights_by_year: Counter = (
        _real_flights_by_year if _flight_windows
        else Counter(int(leg[0]) for leg in _flight_legs)
    )

    # A short, evocative line I actually wrote that year — pulled from the real
    # shout archive, escaped for HTML, and picked deterministically so it stays
    # stable across rebuilds.  Prefer the shortest substantive one-liner.
    def _esc_shout(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    _shout_by_year: dict[int, str] = {}
    _shout_pool: dict[int, list[str]] = defaultdict(list)
    for _rec in shout_records(rows):
        _txt = (_rec.get("text") or "").strip()
        if not (12 <= len(_txt) <= 80) or len(_txt.split()) < 3:
            continue
        if "http" in _txt.lower():
            continue
        try:
            _sy = datetime.fromtimestamp(int(_rec["ts"]), tz=timezone.utc).year
        except (ValueError, OSError, KeyError):
            continue
        _shout_pool[_sy].append(_txt)
    for _sy, _cands in _shout_pool.items():
        # Shortest first, then alphabetical — deterministic and clean.
        _shout_by_year[_sy] = _esc_shout(sorted(_cands, key=lambda s: (len(s), s))[0])

    year_summaries = []
    for yr in sorted({d.year for d in dates}):
        rows_y = _yr_rows.get(yr, [])
        if not rows_y:
            continue
        cat_y = Counter()
        ven_y = Counter()
        cty_y = Counter()
        cou_y = Counter()
        mon_y = Counter()
        comp_y: Counter = Counter()
        for r in rows_y:
            try:
                d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            except (ValueError, OSError):
                continue
            mon_y[d.month] += 1
            if r.get("category"):
                cat_y[r["category"]] += 1
            if r.get("venue_id"):
                ven_y[(r["venue_id"], r.get("venue", ""))] += 1
            if r.get("city"):
                cty_y[r["city"]] += 1
            if r.get("country"):
                cou_y[r["country"]] += 1
            for n in collect_companions(r):
                comp_y[n] += 1
        peak_mon = mon_y.most_common(1)[0] if mon_y else (0, 0)
        top_v = ven_y.most_common(1)[0] if ven_y else ((None, ""), 0)
        top_c = cat_y.most_common(1)[0] if cat_y else ("", 0)
        top_city = cty_y.most_common(1)[0] if cty_y else ("", 0)
        peak_month_name = _months_full[peak_mon[0] - 1] if peak_mon[0] else ""
        n_new_countries = len([c for c, y in _first_country.items() if y == yr])
        n_distance_y = next((int(v) for k, v in dist_by_year if k == str(yr)), 0)
        trips_y = [t for t in trips if t.get("start_year") == yr]
        n_trips_y = len(trips_y)
        new_countries_list = sorted([c for c, y in _first_country.items() if y == yr])
        # First-time cities this year, sampled by how much they mattered
        new_cities_set = {c for c, y in _first_city.items() if y == yr}
        new_city_sample = [c for c, _ in cty_y.most_common()
                           if c in new_cities_set and c != top_city[0]][:2]
        # Farthest reach from home + the year's opening/closing check-ins
        far_city, far_km = "", 0.0
        for r in rows_y:
            try:
                la, ln = float(r["lat"]), float(r["lng"])
            except (ValueError, KeyError, TypeError):
                continue
            if la == 0.0 and ln == 0.0:
                continue
            d_km = _haversine(_HOME_LAT, _HOME_LNG, la, ln)
            if d_km > far_km:
                far_km = d_km
                far_city = r.get("city", "").strip() or r.get("country", "").strip()

        def _stop(r: dict) -> tuple[str, str, str]:
            try:
                mon = _months_full[datetime.fromtimestamp(
                    int(r["date"]), tz=timezone.utc).month - 1]
            except (ValueError, OSError):
                mon = ""
            return (r.get("venue", "").strip(), r.get("city", "").strip(), mon)

        rows_y_sorted = sorted(rows_y, key=lambda r: int(r.get("date", "0") or "0"))
        flights_y = _flights_by_year.get(yr, 0)
        mode_km_y = _mode_km_by_year.get(yr)
        # Was any of this year's travel done by bike?  Trip tags may arrive as a
        # list or as a JSON-encoded string depending on the source.
        bicycle_y = False
        for _t in trips_y:
            _tags = _t.get("tags")
            if isinstance(_tags, str):
                try:
                    _tags = json.loads(_tags)
                except (ValueError, TypeError):
                    _tags = []
            if any("bicycle" in str(tg).lower() or "bike" in str(tg).lower()
                   for tg in (_tags or [])):
                bicycle_y = True
                break
        vivid = _vivid(
            yr=yr, total=len(rows_y),
            peak_mon_name=peak_month_name, peak_mon_n=peak_mon[1],
            top_city=top_city[0], top_city_n=top_city[1],
            top_cat=top_c[0], top_cat_n=top_c[1],
            top_venue=top_v[0][1] if top_v[0] else "", top_venue_n=top_v[1] if top_v else 0,
            n_new_countries=n_new_countries,
            n_cities=len(cty_y), n_countries=len(cou_y),
            top_companion=comp_y.most_common(1)[0][0] if comp_y else "",
            first_new_country=new_countries_list[0] if new_countries_list else "",
            trips_y=trips_y,
            distance_km=n_distance_y,
            n_new_cities=len(new_cities_set),
            new_city_sample=new_city_sample,
            first_stop=_stop(rows_y_sorted[0]) if rows_y_sorted else ("", "", ""),
            last_stop=_stop(rows_y_sorted[-1]) if rows_y_sorted else ("", "", ""),
            farthest_city=far_city, farthest_km=round(far_km),
            n_flights=flights_y, mode_km=mode_km_y, bicycle=bicycle_y,
            shout_quote=_shout_by_year.get(yr, ""),
        )
        year_summaries.append({
            "year":              yr,
            "total":             len(rows_y),
            "peak_month":        peak_month_name[:3] if peak_month_name else "",
            "peak_month_full":   peak_month_name,
            "peak_month_n":      peak_mon[1],
            "top_venue":         top_v[0][1] if top_v[0] else "",
            "top_venue_id":      top_v[0][0] if top_v[0] else "",
            "top_venue_n":       top_v[1] if top_v else 0,
            "top_cat":           top_c[0],
            "top_cat_n":         top_c[1],
            "top_city":          top_city[0],
            "top_city_n":        top_city[1],
            "new_countries":     n_new_countries,
            "new_countries_list": new_countries_list,
            "cities":            len(cty_y),
            "countries":         len(cou_y),
            "vivid":             vivid,
            "distance_km":       n_distance_y,
            "trip_count":        n_trips_y,
            "top_companion":     comp_y.most_common(1)[0][0] if comp_y else "",
            "new_cities":        len(new_cities_set),
            "farthest_city":     far_city,
            "farthest_km":       round(far_km),
        })

    # ── Tier 3.1 — city_inferred KPI ─────────────────────────────────────────
    inferred_n = sum(1 for r in rows if str(r.get("city_inferred", "0")) == "1")
    city_inferred_kpis = {
        "inferred_n":    inferred_n,
        "total":         len(rows),
        "inferred_pct":  round(inferred_n * 100 / len(rows), 1) if rows else 0,
    }

    log.info("Cities: %d | Countries: %d | Unique places: %d | Trips: %d",
             len(cities), len(countries), unique_count, len(trips))
    if shout_stats:
        log.info("Shouts: %d analyzed | %d words | %d emoji types",
                 shout_stats.get("total_shouts", 0),
                 shout_stats.get("total_words", 0),
                 len(shout_stats.get("top_emojis", [])))

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
        "cities_by_venues":   cities_by_venues,
        "city_centroids":     city_centroids,
        "country_centroids":  country_centroids,
        "venues":             venues_list,
        "cat_groups":         cat_groups.most_common(),
        "explorer_cats":      explorer_cats,
        "explorer_groups":    explorer_groups,
        "explorer":           explorer,
        "unique_places":      unique_places,
        "venues_heatmap":     venues_heatmap,
        "companions":         companions,
        "solo_vs_group_by_year": solo_vs_group_by_year,
        "solo_vs_group_totals":  solo_vs_group_totals,
        "companion_countries":   companion_countries,
        "heatmap":            heatmap,
        "discovery_rate":     discovery_rate,
        "venue_loyalty":      venue_loyalty,
        "timeline":           timeline,
        "trips_count":        len(trips),
        "recent":             recent,
        "on_this_day":        on_this_day,
        "venue_freq_dist":    venue_freq_dist,
        "venue_regulars":     venue_regulars,
        "revisit_intervals":  revisit_intervals,
        "dist_by_year":       dist_by_year,
        "total_km":           total_km,
        "longest_streak":     longest_streak,
        "current_streak":     current_streak,
        "new_country_by_year":new_country_by_year,
        "countries_per_year": countries_per_year,
        "cat_drift":          cat_drift,
        "cat_drift_groups":   _top_grps,
        "trip_duration_hist": trip_duration_hist,
        "trip_countries_dist":trip_countries_dist,
        "trip_top_longest":   trip_top_longest,
        "trip_kpis":          trip_kpis,
        "shout_stats":        shout_stats,
        "cross_dim":          cross_dim,
        # ── New tier-1/2/3/5/6 analytics ──
        "transport_modes":    transport_modes,
        "transport_co2":      transport_co2,
        "transport_kpis":     transport_kpis,
        "flight_legs":        flight_legs,
        "cohort_retention":   cohort_retention,
        "distance_from_home": distance_from_home,
        "nomad_kpis":         nomad_kpis,
        "daily_extremes":     daily_extremes,
        "companion_lifecycle":companion_lifecycle,
        "city_funnel":        city_funnel,
        "cat_trajectory_abs": cat_trajectory_abs,
        "cat_trajectory_years": cat_trajectory_years,
        "source_app_by_year": source_app_by_year,
        "diversity_by_year":  diversity_by_year,
        "dormant_venues":     dormant_venues,
        "year_summaries":     year_summaries,
        "city_inferred_kpis": city_inferred_kpis,
    }
    return stats, trips
