"""Index "pulse" card: this month so far against the same days a year earlier,
and the first-ever places, cities, countries and categories of the last 30 days.

All dates are the check-in's LOCAL date (the caller passes the localiser), and
"today" is the newest check-in's local day, so a rebuild without new check-ins
reads the same.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

WINDOW_DAYS = 30
SAMPLE = 8
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _venue_key(r: dict) -> str:
    return (r.get("venue_id") or "").strip()


def _facts(rows: list[dict], first_venue_rows: set[int]) -> dict:
    venues = {_venue_key(r) for r in rows} - {""}
    cities = {((r.get("country") or "").strip(), (r.get("city") or "").strip()) for r in rows}
    return {
        "checkins": len(rows),
        "venues": len(venues),
        "new_venues": sum(1 for r in rows if id(r) in first_venue_rows),
        "cities": len({c for c in cities if c[1]}),
        "countries": len({(r.get("country") or "").strip() for r in rows} - {""}),
    }


def pulse(rows: list[dict], local_dt: Callable[[dict], datetime | None],
          window_days: int = WINDOW_DAYS) -> dict:
    items: list[tuple[datetime, dict]] = []
    for r in rows:
        try:
            d = local_dt(r)
        except (KeyError, TypeError, ValueError, OSError):
            d = None
        if d is not None:
            items.append((d, r))
    if not items:
        return {}
    items.sort(key=lambda t: t[0].timestamp())

    # first-ever occurrence of each venue / city / country / category
    seen: dict[str, set] = {"venue": set(), "city": set(), "country": set(), "category": set()}
    firsts: dict[str, list[tuple[datetime, dict]]] = {k: [] for k in seen}
    for d, r in items:
        keys = {
            "venue": _venue_key(r),
            "city": ((r.get("country") or "").strip(), (r.get("city") or "").strip())
            if (r.get("city") or "").strip() else "",
            "country": (r.get("country") or "").strip(),
            "category": (r.get("category") or "").strip(),
        }
        for kind, k in keys.items():
            if k and k not in seen[kind]:
                seen[kind].add(k)
                firsts[kind].append((d, r))
    first_venue_rows = {id(r) for _, r in firsts["venue"]}

    latest = items[-1][0]
    today = latest.date()

    def month_rows(year: int) -> list[dict]:
        return [r for d, r in items
                if d.year == year and d.month == today.month and d.day <= today.day]

    cutoff = latest - timedelta(days=window_days)
    recent = {k: [(d, r) for d, r in v if d > cutoff] for k, v in firsts.items()}
    venue_sample = [[(r.get("venue") or "").strip(), (r.get("city") or "").strip(), _venue_key(r)]
                    for _, r in reversed(recent["venue"][-SAMPLE:])]
    return {
        "as_of": today.isoformat(),
        "month": {
            "label": f"{_MONTHS[today.month - 1]} 1–{today.day}",
            "this": {"year": today.year, **_facts(month_rows(today.year), first_venue_rows)},
            "prev": {"year": today.year - 1, **_facts(month_rows(today.year - 1), first_venue_rows)},
        },
        "firsts": {
            "window": window_days,
            "venues": len(recent["venue"]),
            "venue_sample": venue_sample,
            "cities": [[r["city"].strip(), (r.get("country") or "").strip()] for _, r in recent["city"]],
            "countries": [(r.get("country") or "").strip() for _, r in recent["country"]],
            "categories": [(r.get("category") or "").strip() for _, r in recent["category"]],
        },
    }
