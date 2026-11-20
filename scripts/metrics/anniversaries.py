"""Index "coming up" line: round-the-calendar anniversaries of firsts in the next
few days — the first check-in ever, the first day in each country, the first day
in each city with a page of its own (CITY_MIN check-ins).

Dates are the check-in's LOCAL date (the caller passes the localiser); `today`
is the build's date, so the line moves with the hourly rebuild.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime

DAYS_AHEAD = 7
CITY_MIN = 20
LIMIT = 6


def _on(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:                      # 29 February in a common year
        return d.replace(year=year, day=28)


def anniversaries(rows: list[dict], local_dt: Callable[[dict], datetime], today: date,
                  days_ahead: int = DAYS_AHEAD, city_min: int = CITY_MIN, limit: int = LIMIT) -> list[dict]:
    items: list[tuple[datetime, dict]] = []
    for r in rows:
        try:
            items.append((local_dt(r), r))
        except (KeyError, TypeError, ValueError, OSError):
            continue
    if not items:
        return []
    items.sort(key=lambda t: t[0].timestamp())
    city_n = Counter((r.get("city") or "").strip() for _, r in items)

    firsts: list[tuple[str, str, str, date]] = [("first", "", "", items[0][0].date())]
    seen_c: set[str] = set()
    seen_y: set[str] = set()
    for d, r in items:
        co = (r.get("country") or "").strip()
        cy = (r.get("city") or "").strip()
        if co and co not in seen_c:
            seen_c.add(co)
            firsts.append(("country", co, co, d.date()))
        if cy and cy not in seen_y:
            seen_y.add(cy)
            if city_n[cy] >= city_min:
                firsts.append(("city", cy, co, d.date()))

    out: list[tuple[int, int, int, str, dict]] = []
    rank = {"first": 0, "country": 1, "city": 2}
    for kind, name, country, d in firsts:
        ann = _on(d, today.year)
        if ann < today:
            ann = _on(d, today.year + 1)
        years = ann.year - d.year
        delta = (ann - today).days
        if years >= 1 and 0 <= delta <= days_ahead:
            out.append((delta, -years, rank[kind], name,
                        {"kind": kind, "name": name, "country": country, "years": years,
                         "in_days": delta, "first": d.isoformat()}))
    out.sort(key=lambda t: t[:4])
    return [t[4] for t in out[:limit]]
