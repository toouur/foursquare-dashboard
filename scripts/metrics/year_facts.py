# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Comparable facts per year and per home era, for compare.html (metrics package).

Every bucket — a calendar year (local date) or a home era from
`trip_detection.home_history` — gets the same fact sheet, so any two can be put
side by side. Eras are computed from their own rows (by ts, era-aware via
`trips._home_at`), not summed from years: unique counts (venues, cities,
companions) are not additive, and an era boundary falls mid-year.

"new_*" means first-ever in the whole history, so a year's new venues plus the
other years' new venues add up to the lifetime total.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from .companions import collect_companions
from .milestones import _f, _Localiser
from .trips import _home_at, _parse_ts

REFURB = "refurbished"
HOME_CATEGORY = "Home (private)"
TOP = 8


def _top(c: Counter, n: int = TOP) -> list[list]:
    return [[k, v] for k, v in c.most_common(n)]


def _facts(items: list[tuple[datetime, dict]], firsts: dict[str, dict[str, int]],
           photos_by_checkin: dict, home_city: str, home_history: list) -> dict:
    venues: set[str] = set()
    cities: Counter[str] = Counter()
    countries: Counter[str] = Counter()
    cats: Counter[str] = Counter()
    vnames: Counter[str] = Counter()
    comp: Counter[str] = Counter()
    hours = [0] * 24
    wdays = [0] * 7
    days: set = set()
    new = {"venues": 0, "cities": 0, "countries": []}  # type: dict
    real = shouts = photos = at_home = 0
    for ld, r in items:
        vid = (r.get("venue_id") or "").strip()
        city = (r.get("city") or "").strip()
        country = (r.get("country") or "").strip()
        cat = (r.get("category") or "").strip()
        is_rf = (r.get("source_app") or "") == REFURB
        days.add(ld.date())
        if vid:
            venues.add(vid)
            if firsts["venue"].get(vid) == id(r):
                new["venues"] += 1
        if city:
            cities[city] += 1
            if firsts["city"].get(city) == id(r):
                new["cities"] += 1
            if city == _home_at(int(ld.timestamp()), home_city, home_history):
                at_home += 1
        if country:
            countries[country] += 1
            if firsts["country"].get(country) == id(r):
                new["countries"].append(country)
        if cat:
            cats[cat] += 1
        if vid and cat != HOME_CATEGORY and r.get("venue"):
            vnames[r["venue"]] += 1
        comp.update(collect_companions(r))
        if (r.get("shout") or "").strip():
            shouts += 1
        photos += len(photos_by_checkin.get(r.get("checkin_id") or "") or [])
        if not is_rf:
            real += 1
            hours[ld.hour] += 1
            wdays[ld.weekday()] += 1
    n = len(items)
    return {
        "checkins": n, "reconstructed": n - real, "days": len(days),
        "per_day": round(n / len(days), 2) if days else 0,
        "venues": len(venues), "new_venues": new["venues"],
        "cities": len(cities), "new_cities": new["cities"],
        "countries": len(countries), "new_countries": new["countries"],
        "home_pct": round(100 * at_home / n, 1) if n else 0,
        "companions": len(comp), "shouts": shouts, "photos": photos,
        "top_categories": _top(cats), "top_cities": _top(cities), "top_venues": _top(vnames, 5),
        "top_companions": _top(comp, 5), "top_countries": _top(countries),
        "hours": hours, "weekdays": wdays,
    }


def compute_year_facts(rows: list[dict], home_city: str, home_history: list[tuple[int, str]] | None = None,
                       trips: list[dict] | None = None, photos_by_checkin: dict | None = None,
                       distance_from_home: list | None = None, coverage: dict | None = None) -> dict:
    """Returns ``{years: {"2015": facts}, eras: [{id, label, city, from, to, facts}]}``.

    `from`/`to` are UTC ``YYYY-MM-DD`` (to exclusive, null for the current era).
    Year facts also carry `trips`, `trip_days`, `farthest_km` and `rg_km`; era
    facts carry the same summed over their span.
    """
    home_history = home_history or []
    trips = trips or []
    photos_by_checkin = photos_by_checkin or {}
    loc = _Localiser()
    items: list[tuple[datetime, dict]] = []
    for r in rows:
        d = _parse_ts(r)
        if d is None:
            continue
        ld = loc(d, _f(r.get("lat")), _f(r.get("lng")), (r.get("country") or "").strip())
        items.append((ld, r))
    items.sort(key=lambda t: t[0].timestamp())

    # first occurrence of each venue / city / country, by row identity
    firsts: dict[str, dict[str, int]] = {"venue": {}, "city": {}, "country": {}}
    for _, r in items:
        for kind, col in (("venue", "venue_id"), ("city", "city"), ("country", "country")):
            v = (r.get(col) or "").strip()
            if v and v not in firsts[kind]:
                firsts[kind][v] = id(r)

    by_year: dict[int, list] = defaultdict(list)
    for it in items:
        by_year[it[0].year].append(it)

    # eras: [start, until) per home_history entry, the last one open-ended
    bounds: list[tuple[int | None, int | None, str]] = []
    prev: int | None = None
    for until, city in home_history:
        bounds.append((prev, until, city))
        prev = until
    bounds.append((prev, None, home_city))
    by_era: list[list] = [[] for _ in bounds]
    for it in items:
        ts = it[0].timestamp()
        for i, (a, b, _c) in enumerate(bounds):
            if (a is None or ts >= a) and (b is None or ts < b):
                by_era[i].append(it)
                break

    dist = [(str(d), float(km)) for d, km in (distance_from_home or [])]
    rg = {y["y"]: y["rg"] for y in (coverage or {}).get("years", [])}

    def _travel(lo: str, hi: str | None) -> dict:
        ts_ = [t for t in trips if lo <= (t.get("start_date") or "") and (hi is None or t["start_date"] < hi)]
        km = [k for d, k in dist if lo <= d and (hi is None or d < hi)]
        return {"trips": len(ts_), "trip_days": sum(int(t.get("duration") or 0) for t in ts_),
                "farthest_km": round(max(km)) if km else 0}

    years = {}
    for y in sorted(by_year):
        f = _facts(by_year[y], firsts, photos_by_checkin, home_city, home_history)
        f.update(_travel(f"{y}-01-01", f"{y + 1}-01-01"))
        f["rg_km"] = rg.get(y)
        years[str(y)] = f

    def _day(ts: int | None) -> str | None:
        return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    eras = []
    for i, ((a, b, city), its) in enumerate(zip(bounds, by_era)):
        if not its:
            continue
        f = _facts(its, firsts, photos_by_checkin, home_city, home_history)
        f.update(_travel(_day(a) or "0000-00-00", _day(b)))
        f["rg_km"] = None
        y0, y1 = its[0][0].year, its[-1][0].year
        eras.append({"id": f"era{i + 1}", "city": city, "from": _day(a), "to": _day(b),
                     "label": f"{city} {y0}–{y1}" if y1 != y0 else f"{city} {y0}", "facts": f})
    return {"years": years, "eras": eras}
