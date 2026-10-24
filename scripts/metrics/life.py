# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

""""Life in stripes" — the whole history as parallel stripes, for life.html (metrics package).

One shared time axis (local calendar days, first check-in → last) and a few bands:

* home    — home eras from `trip_detection.home_history` (era-aware, like everything)
* country — where each day was spent: the day's most-checked-in country; a day
            without check-ins inherits the previous one (flagged `inferred`), so
            the band has no holes. Run-length encoded.
* trips   — detected trips (start/end date, name, id for the trips.html deep link)
* density — check-ins per ISO week
* people  — for the most frequent companions, check-ins together per month
* music   — top Last.fm artist per year, when `lastfm_years.json` is present
            (years before the first check-in are dropped)

Days are indices from `start` so the page needs no date maths beyond one offset.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .companions import collect_companions
from .milestones import _f, _Localiser
from .trips import _parse_ts

TOP_PEOPLE = 8


def compute_life(rows: list[dict], home_city: str, home_history: list[tuple[int, str]] | None = None,
                 trips: list[dict] | None = None, music_by_year: dict | None = None) -> dict:
    home_history = home_history or []
    loc = _Localiser()
    day_c: dict[date, Counter[str]] = defaultdict(Counter)
    day_n: Counter[date] = Counter()
    people_m: dict[str, Counter[str]] = defaultdict(Counter)
    people_tot: Counter[str] = Counter()
    for r in rows:
        d = _parse_ts(r)
        if d is None:
            continue
        country = (r.get("country") or "").strip()
        ld = loc(d, _f(r.get("lat")), _f(r.get("lng")), country).date()
        day_n[ld] += 1
        if country:
            day_c[ld][country] += 1
        for p in collect_companions(r):
            people_m[p][ld.strftime("%Y-%m")] += 1
            people_tot[p] += 1
    if not day_n:
        return {}

    start, end = min(day_n), max(day_n)
    n_days = (end - start).days + 1

    countries: list[str] = []
    cidx: dict[str, int] = {}
    runs: list[list[int]] = []          # [first_day, length, country_index, inferred]
    cur: int | None = None
    for i in range(n_days):
        dd = start + timedelta(days=i)
        if day_c.get(dd):
            name = day_c[dd].most_common(1)[0][0]
            if name not in cidx:
                cidx[name] = len(countries)
                countries.append(name)
            cur, inferred = cidx[name], 0
        else:
            inferred = 1
        if cur is None:
            continue
        if runs and runs[-1][2] == cur and runs[-1][3] == inferred and runs[-1][0] + runs[-1][1] == i:
            runs[-1][1] += 1
        else:
            runs.append([i, 1, cur, inferred])

    def _idx(s: str | None) -> int | None:
        if not s:
            return None
        try:
            return (date.fromisoformat(s[:10]) - start).days
        except ValueError:
            return None

    # home eras: [from, until) clipped to the axis
    eras = []
    prev: int | None = None
    for until, city in [*home_history, (None, home_city)]:
        a = 0 if prev is None else max(0, _idx(_utc_day(prev)) or 0)
        b = n_days if until is None else min(n_days, _idx(_utc_day(until)) or 0)
        if b > a:
            eras.append([a, b - a, city])
        prev = until

    trip_list = []
    for t in trips or []:
        a, b = _idx(t.get("start_date")), _idx(t.get("end_date"))
        if a is None or b is None:
            continue
        trip_list.append([a, max(1, b - a + 1), t.get("name") or "", t.get("id")])

    # weekly density on the same axis: week k covers days [7k, 7k+7)
    weeks = [0] * ((n_days + 6) // 7)
    for dd, n in day_n.items():
        weeks[(dd - start).days // 7] += n

    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    people = [[p, [people_m[p].get(mm, 0) for mm in months]] for p, _ in people_tot.most_common(TOP_PEOPLE)]

    music = []
    for yr, v in sorted((music_by_year or {}).items()):
        if not str(yr).isdigit() or int(yr) < start.year:   # zero-timestamp scrobbles land in 1970
            continue
        top = (v or {}).get("top_artist") or {}
        if top.get("name"):
            music.append([str(yr), top["name"], int(top.get("plays") or 0), int(v.get("scrobbles") or 0)])

    return {
        "start": start.isoformat(), "days": n_days,
        "countries": countries, "country_runs": runs,
        "eras": eras, "trips": trip_list, "weeks": weeks,
        "months": months, "people": people, "music": music,
    }


def _utc_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
