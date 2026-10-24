# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Records & milestones board (metrics package).

Replays the whole history once and labels every check-in exactly the way the
Telegram check-in echo bot (`scripts/telegram_checkin_echo.py`) labels a new one:
first time in a country / city, back in a city after a year, comeback to a venue,
the 10th/25th/50th/… visit, every 1000th check-in. The rule constants are
mirrored here (metrics must not import the telegram scripts) and pinned against
the bot by `tests/test_milestones.py`.

The bot answers "what is new about THIS check-in" with an O(n) scan per row; here
the same facts are folded incrementally, so the whole history costs one pass.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .trips import _COUNTRY_TZ, _parse_ts, _tz_at

log = logging.getLogger(__name__)

# ── Mirrored from telegram_checkin_echo.py ────────────────────────────────────
INSTANT_MILESTONES = {50, 100, 250, 500, 1000}
DIGEST_MILESTONES = {10, 25}
COMEBACK_INSTANT_DAYS = 5 * 365
COMEBACK_DIGEST_DAYS = 365
TOTAL_STEP = 1000
REFURB = "refurbished"
HOME_CATEGORY = "Home (private)"
PASS_THROUGH = {
    "Road", "Street", "Intersection", "Bridge", "Bus Line", "Bus Stop",
    "Tram Station", "Metro Station", "Light Rail Station", "Train", "Taxi",
    "Neighborhood", "City", "Town", "Village", "County", "State", "Country",
    "Highway", "Moving Target",
}
RANK = {
    "new_country": 0, "new_city": 1, "city_back": 1, "total": 2,
    "milestone_big": 3, "comeback_long": 4, "milestone": 5, "comeback": 6,
    "new_venue": 7,
}


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class _Localiser:
    """`trips._localise` with the timezone lookup cached (timezonefinder is slow)."""

    def __init__(self) -> None:
        self._cache: dict[object, object] = {}

    def __call__(self, d: datetime, lat: float | None, lng: float | None, country: str) -> datetime:
        if country and country in _COUNTRY_TZ:
            key: object = ("c", country)
            name = _COUNTRY_TZ[country]
        else:
            key = ("p", None if lat is None else round(lat, 2), None if lng is None else round(lng, 2))
            name = None
        tz = self._cache.get(key)
        if tz is None:
            try:
                tz = ZoneInfo(name or _tz_at(lat, lng))
            except Exception:
                tz = False
            self._cache[key] = tz
        return d.astimezone(tz) if tz else d  # type: ignore[arg-type]


def labels_for(*, first_in_country: bool, first_in_city: bool, days_since_city: int | None,
               days_since_last: int | None, visit_no: int, checkin_no: int,
               category: str) -> list[str]:
    """The echo bot's `events()` rules, strongest first (by RANK) plus a trailing
    `new_venue` / `visit`."""
    labels: list[str] = []
    if first_in_country:
        labels.append("new_country")
    if first_in_city:
        labels.append("new_city")
    elif days_since_city is not None and days_since_city >= 365:
        labels.append("city_back")
    if days_since_last is not None:
        if days_since_last >= COMEBACK_INSTANT_DAYS:
            labels.append("comeback_long")
        elif days_since_last >= COMEBACK_DIGEST_DAYS:
            labels.append("comeback")
    if visit_no in INSTANT_MILESTONES:
        labels.append("milestone_big")
    elif visit_no in DIGEST_MILESTONES:
        labels.append("milestone")
    if category in PASS_THROUGH:
        labels = ["comeback" if x == "comeback_long" else
                  "milestone" if x == "milestone_big" else x for x in labels]
    if checkin_no % TOTAL_STEP == 0:
        labels.append("total")
    labels.sort(key=lambda x: RANK.get(x, 99))
    labels.append("new_venue" if visit_no == 1 else "visit")
    return labels


def compute_milestones(rows: list[dict], distance_from_home: list | None = None,
                       top_n: int = 400) -> dict:
    """Label the whole history once and derive the records board.

    Returns ``{events, counts, records, firsts_by_year}``:
      events          newest-first labelled real check-ins (plain repeat visits and
                      bare new venues dropped), capped at ``top_n``
      counts          label → how many real check-ins carry it (uncapped)
      records         single-value "personal bests"
      firsts_by_year  year → {countries, cities, venues} first seen that year
    """
    hist: list[tuple[datetime, dict]] = []
    for r in rows:
        d = _parse_ts(r)
        if d is not None:
            hist.append((d, r))
    hist.sort(key=lambda t: t[0])  # stable: equal ts keep their given order

    loc = _Localiser()
    venue_n: Counter[str] = Counter()
    venue_last: dict[str, date] = {}
    city_last: dict[str, date] = {}
    countries: set[str] = set()

    events: list[dict] = []
    counts: Counter[str] = Counter()
    firsts: dict[str, dict[str, int]] = defaultdict(lambda: {"countries": 0, "cities": 0, "venues": 0})
    day_n: Counter[date] = Counter()
    day_countries: dict[date, set[str]] = defaultdict(set)
    day_cities: dict[date, set[str]] = defaultdict(set)
    real_days: list[date] = []

    for i, (d, r) in enumerate(hist):
        lat, lng = _f(r.get("lat")), _f(r.get("lng"))
        vid = (r.get("venue_id") or "").strip()
        city = (r.get("city") or "").strip()
        country = (r.get("country") or "").strip()
        category = (r.get("category") or "").strip()
        ld = loc(d, lat, lng, country).date()

        visit_no = (venue_n[vid] if vid else 0) + 1
        days_since_last = (ld - venue_last[vid]).days if vid and vid in venue_last else None
        days_since_city = (ld - city_last[city]).days if city and city in city_last else None
        first_in_city = bool(city) and city not in city_last
        first_in_country = bool(country) and country not in countries

        real = (r.get("source_app") or "") != REFURB and category != HOME_CATEGORY
        if real:
            labels = labels_for(first_in_country=first_in_country, first_in_city=first_in_city,
                                days_since_city=days_since_city, days_since_last=days_since_last,
                                visit_no=visit_no, checkin_no=i + 1, category=category)
            counts.update(labels)
            yr = str(ld.year)
            if first_in_country:
                firsts[yr]["countries"] += 1
            if first_in_city:
                firsts[yr]["cities"] += 1
            if visit_no == 1:
                firsts[yr]["venues"] += 1
            if labels[0] not in ("visit", "new_venue"):
                if "comeback" in labels or "comeback_long" in labels:
                    gap = days_since_last
                elif "city_back" in labels:
                    gap = days_since_city
                else:
                    gap = None
                events.append({
                    "ts": int(d.timestamp()), "date": ld.isoformat(),
                    "labels": labels[:-1], "label": labels[0],
                    "venue": r.get("venue") or "", "venue_id": vid,
                    "city": city, "country": country, "category": category,
                    "visit_no": visit_no, "checkin_no": i + 1, "gap_days": gap,
                })
        if (r.get("source_app") or "") != REFURB:
            day_n[ld] += 1
            if country:
                day_countries[ld].add(country)
            if city:
                day_cities[ld].add(city)
            real_days.append(ld)

        if vid:
            venue_n[vid] += 1
            venue_last[vid] = ld
        if city:
            city_last[city] = ld
        if country:
            countries.add(country)

    events.reverse()

    records: dict[str, dict] = {}
    if distance_from_home:
        try:
            dd, km = max(distance_from_home, key=lambda x: float(x[1]))
            records["farthest_day"] = {"date": str(dd), "km": round(float(km))}
        except (ValueError, TypeError, IndexError):
            pass
    if day_n:
        bd, bn = max(day_n.items(), key=lambda kv: (kv[1], kv[0]))
        records["busiest_day"] = {"date": bd.isoformat(), "n": bn,
                                  "cities": sorted(day_cities.get(bd, ()))}
    if day_countries:
        cd, cs = max(day_countries.items(), key=lambda kv: (len(kv[1]), kv[0]))
        records["most_countries_day"] = {"date": cd.isoformat(), "n": len(cs), "countries": sorted(cs)}
    if day_cities:
        ccd, ccs = max(day_cities.items(), key=lambda kv: (len(kv[1]), kv[0]))
        records["most_cities_day"] = {"date": ccd.isoformat(), "n": len(ccs), "cities": sorted(ccs)}
    if len(real_days) > 1:
        uniq = sorted(set(real_days))
        best = max(zip(uniq, uniq[1:]), key=lambda p: ((p[1] - p[0]).days, p[0]))
        records["longest_silence"] = {"from": best[0].isoformat(), "to": best[1].isoformat(),
                                      "days": (best[1] - best[0]).days}
    top_v: Counter[str] = Counter()
    top_name: dict[str, str] = {}
    for _, r in hist:
        vid = (r.get("venue_id") or "").strip()
        if vid and (r.get("category") or "") != HOME_CATEGORY and (r.get("source_app") or "") != REFURB:
            top_v[vid] += 1
            top_name[vid] = r.get("venue") or ""
    if top_v:
        vid, n = top_v.most_common(1)[0]
        records["top_venue"] = {"venue_id": vid, "venue": top_name[vid], "n": n}
    comebacks = [e for e in events if e["gap_days"] and
                 ("comeback" in e["labels"] or "comeback_long" in e["labels"])]
    if comebacks:
        e = max(comebacks, key=lambda e: e["gap_days"])
        records["longest_comeback"] = {"venue": e["venue"], "venue_id": e["venue_id"],
                                       "date": e["date"], "days": e["gap_days"]}

    return {
        "events": events[:top_n],
        "counts": dict(counts),
        "records": records,
        "firsts_by_year": {y: firsts[y] for y in sorted(firsts)},
    }
