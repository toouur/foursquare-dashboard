# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Trip insights — three read-only views over the detected trips.

  * ``era_comparison``   — one row per home era (Chișinău → Minsk → Chișinău):
                           how often and how long you travelled from each home.
  * ``trip_gateways``    — which stations / airports open and close trips.
  * ``trip_seasonality`` — days away per month, year by year.

Pure functions of the finished trip list: nothing here feeds back into trip
detection, names or any other metric, so adding them cannot move a trip.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median

# A gateway is a place a journey physically leaves from / arrives at. Border
# crossings, roads and taxis are deliberately absent — they are passed through,
# not departed from.
_GATEWAY_MODE: dict[str, str] = {
    "Airport":                "✈️",
    "International Airport":  "✈️",
    "Airport Terminal":       "✈️",
    "Airport Gate":           "✈️",
    "Airport Service":        "✈️",
    "Airport Lounge":         "✈️",
    "Airport Ticket Counter": "✈️",
    "Baggage Claim":          "✈️",
    "Rail Station":           "🚂",
    "Train Station":          "🚂",
    "Bus Station":            "🚌",
    "Bus Terminal":           "🚌",
    "Ferry Terminal":         "⛴️",
    "Harbor or Marina":       "⛴️",
}

# How deep into each end of a trip to look for its gateway: the first hub
# usually sits behind a taxi / a coffee / a "City" check-in, never much deeper.
_GATEWAY_PROBE = 6


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _trip_days(t: dict) -> list[date]:
    try:
        a = date.fromisoformat(t["start_date"])
        b = date.fromisoformat(t["end_date"])
    except (KeyError, TypeError, ValueError):
        return []
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def _gateway(checkins: list[dict]) -> dict | None:
    for c in checkins:
        mode = _GATEWAY_MODE.get(c.get("category", ""))
        if mode:
            return {**c, "mode": mode}
    return None


def _gw_key(c: dict) -> str:
    return c.get("venue_id") or f"{c.get('venue', '')}|{c.get('city', '')}"


def trip_gateways(trips: list[dict], top: int = 12) -> list[dict]:
    """Rank hubs by how many trips they opened (departure) and closed (arrival)."""
    acc: dict[str, dict] = {}
    for t in trips:
        cks = t.get("checkins") or []
        if not cks:
            continue
        ends = (
            ("opened", _gateway(cks[:_GATEWAY_PROBE])),
            ("closed", _gateway(list(reversed(cks[-_GATEWAY_PROBE:])))),
        )
        for side, g in ends:
            if not g:
                continue
            e = acc.setdefault(_gw_key(g), {
                "venue": g.get("venue", ""), "city": g.get("city", ""),
                "country": g.get("country", ""), "mode": g["mode"],
                "opened": 0, "closed": 0, "first": t["start_year"], "last": t["start_year"],
            })
            e[side] += 1
            e["first"] = min(e["first"], t["start_year"])
            e["last"] = max(e["last"], t["start_year"])
    ranked = sorted(acc.values(), key=lambda e: (-(e["opened"] + e["closed"]), -e["opened"], e["venue"]))
    return ranked[:top]


def trip_seasonality(trips: list[dict]) -> dict:
    """Days spent on trips per (year, month), plus trip starts per month."""
    days_ym: Counter = Counter()
    starts = [0] * 12
    for t in trips:
        for d in _trip_days(t):
            days_ym[(d.year, d.month)] += 1
        try:
            starts[date.fromisoformat(t["start_date"]).month - 1] += 1
        except (KeyError, TypeError, ValueError):
            pass
    if not days_ym:
        return {}
    years = list(range(min(y for y, _ in days_ym), max(y for y, _ in days_ym) + 1))
    days = [[days_ym.get((y, m), 0) for m in range(1, 13)] for y in years]
    month_tot = [sum(row[m] for row in days) for m in range(12)]
    return {
        "years":      years,
        "days":       days,
        "month_days": month_tot,
        "starts":     starts,
        "peak_month": max(range(12), key=lambda m: month_tot[m]),
    }


def era_comparison(
    trips: list[dict],
    rows: list[dict],
    home_city: str,
    home_history: list[tuple[int, str]] | None,
    city_country: dict[str, str],
) -> list[dict]:
    """One summary per home era that actually holds check-ins.

    An era owns every trip that STARTS inside it (a relocation trip belongs to
    the home it left), while days-away are counted by calendar day so the
    percentage always shares its denominator with the era's own span.
    """
    ts_all = []
    refurb_by_day: Counter = Counter()
    total_by_day: Counter = Counter()
    for r in rows:
        try:
            ts = int(r.get("date") or 0)
        except (TypeError, ValueError):
            continue
        if not ts:
            continue
        ts_all.append(ts)
        d = _day(ts)
        total_by_day[d] += 1
        if (r.get("source_app") or "") == "refurbished":
            refurb_by_day[d] += 1
    if not ts_all:
        return []
    first_day, last_day = _day(min(ts_all)), _day(max(ts_all))

    # Era spans as [start, end) days; the final era runs to the data's end.
    bounds: list[tuple[date | None, date | None, str]] = []
    prev: date | None = None
    for until_ts, city in (home_history or []):
        until = _day(until_ts)
        bounds.append((prev, until, city))
        prev = until
    bounds.append((prev, None, home_city))

    away_days: set[date] = set()
    for t in trips:
        away_days.update(_trip_days(t))

    out: list[dict] = []
    for start, end, city in bounds:
        lo = max(first_day, start) if start else first_day
        hi = min(last_day + timedelta(days=1), end) if end else last_day + timedelta(days=1)
        span = (hi - lo).days
        if span <= 0:
            continue
        lo_ts = int(datetime(lo.year, lo.month, lo.day, tzinfo=timezone.utc).timestamp())
        hi_ts = int(datetime(hi.year, hi.month, hi.day, tzinfo=timezone.utc).timestamp())
        era_trips = [t for t in trips if lo_ts <= int(t.get("start_ts") or 0) < hi_ts]
        home_country = city_country.get(city, "")
        durs = [int(t.get("duration") or 0) for t in era_trips]
        away = sum(1 for d in away_days if lo <= d < hi)

        dest: Counter = Counter()
        for t in era_trips:
            for co in set(t.get("countries") or []):
                if co and co != home_country:
                    dest[co] += 1
        gw = trip_gateways(era_trips, top=1)
        n_rows = sum(n for d, n in total_by_day.items() if lo <= d < hi)
        n_ref = sum(n for d, n in refurb_by_day.items() if lo <= d < hi)

        out.append({
            "city":           city,
            "country":        home_country,
            "from":           lo.isoformat(),
            "to":             (hi - timedelta(days=1)).isoformat(),
            "days":           span,
            "trips":          len(era_trips),
            # A rate extrapolated from a few weeks is noise, not a habit.
            "trips_per_year": round(len(era_trips) / (span / 365.25), 1) if span >= 180 else None,
            "days_away":      away,
            "away_pct":       round(100 * away / span, 1),
            "avg_days":       round(sum(durs) / len(durs), 1) if durs else 0,
            "median_days":    median(durs) if durs else 0,
            "countries":      len(dest),
            "top_dest":       list(dest.most_common(1)[0]) if dest else None,
            "top_gateway":    ({"venue": gw[0]["venue"], "mode": gw[0]["mode"],
                                "n": gw[0]["opened"] + gw[0]["closed"]} if gw else None),
            "reconstructed_pct": round(100 * n_ref / n_rows) if n_rows else 0,
        })
    return out


def trip_insights(
    trips: list[dict],
    rows: list[dict],
    home_city: str,
    home_history: list[tuple[int, str]] | None,
    city_country: dict[str, str],
) -> dict:
    """All three views, keyed the way process() exposes them in stats JSON."""
    if not trips:
        return {"trip_eras": [], "trip_gateways": [], "trip_seasonality": {}}
    return {
        "trip_eras":        era_comparison(trips, rows, home_city, home_history, city_country),
        "trip_gateways":    trip_gateways(trips),
        "trip_seasonality": trip_seasonality(trips),
    }


__all__ = ["trip_insights", "era_comparison", "trip_gateways", "trip_seasonality"]
