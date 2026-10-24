# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""City coverage + radius of gyration (metrics package).

Two "how far does life reach" views for stats.html:

* radius of gyration per year and per month — ``sqrt(mean(d²))`` of every real
  check-in's distance from the home of THAT moment (era-aware, see
  `trips._home_at`), so the Chișinău and Minsk years are each measured from their
  own home. Months are bucketed 1–5 by quantiles over all non-empty months
  (0 = no check-ins), which the template draws as a contributions-style grid.
* city coverage — for the most-visited cities, how many distinct ~500 m cells the
  check-ins touch, and the area that covers.

Reconstructed (`refurbished`) rows are skipped: they sit at day/city granularity
and would read as precise coordinates.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .trips import _home_at, _parse_ts

REFURB = "refurbished"
CELL_DEG = 0.0045          # ≈ 500 m of latitude
CELL_KM2 = 0.25            # 0.5 km × 0.5 km (latitude-corrected below)


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def _bucket(v: float, thresholds: list[float]) -> int:
    b = 1
    for k, t in enumerate(thresholds[1:], start=2):
        if v >= t:
            b = k
    return b


def compute_coverage(rows: list[dict], home_city: str,
                     home_history: list[tuple[int, str]] | None = None,
                     top_cities: int = 12) -> dict:
    """Returns ``{years, thresholds, cities}``:

      years       ``[{y, rg, months: [12 × {n, rg, b}]}]`` oldest first, rg in km
      thresholds  5 quantile cut points (0, q20, q40, q60, q80) of monthly rg
      cities      ``[{city, cells, km2}]`` for the top cities by check-in count
    """
    home_history = home_history or []
    pts: list[tuple[int, float, float, str]] = []
    for r in rows:
        if (r.get("source_app") or "") == REFURB:
            continue
        lat, lng = _f(r.get("lat")), _f(r.get("lng"))
        d = _parse_ts(r)
        if lat is None or lng is None or d is None or (lat == 0 and lng == 0):
            continue
        pts.append((int(d.timestamp()), lat, lng, (r.get("city") or "").strip()))

    # Centroid of each home city from its own check-ins (no network, no gazetteer).
    homes = {home_city, *(c for _, c in home_history)}
    sums: dict[str, list[float]] = {c: [0.0, 0.0, 0] for c in homes}  # type: ignore[misc]
    for _, lat, lng, city in pts:
        if city in sums:
            s = sums[city]
            s[0] += lat; s[1] += lng; s[2] += 1
    centroid = {c: (s[0] / s[2], s[1] / s[2]) for c, s in sums.items() if s[2]}

    month_d2: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ts, lat, lng, _ in pts:
        h = centroid.get(_home_at(ts, home_city, home_history))
        if h is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        month_d2[(dt.year, dt.month)].append(_haversine(lat, lng, h[0], h[1]) ** 2)

    month_rg = {k: math.sqrt(sum(v) / len(v)) for k, v in month_d2.items()}
    vals = sorted(month_rg.values())
    thresholds = [round(_quantile(vals, q), 1) for q in (0.0, 0.2, 0.4, 0.6, 0.8)]

    years = []
    for y in sorted({k[0] for k in month_d2}):
        allv = [d2 for m in range(1, 13) for d2 in month_d2.get((y, m), [])]
        months = []
        for m in range(1, 13):
            n = len(month_d2.get((y, m), []))
            rg = month_rg.get((y, m))
            months.append({"n": n, "rg": round(rg) if rg is not None else 0,
                           "b": _bucket(rg, thresholds) if rg is not None else 0})
        years.append({"y": y, "rg": round(math.sqrt(sum(allv) / len(allv))) if allv else 0,
                      "months": months})

    by_city: Counter[str] = Counter(city for *_, city in pts if city)
    cells: dict[str, set[tuple[int, int]]] = defaultdict(set)
    lat_sum: dict[str, float] = defaultdict(float)
    top = [c for c, _ in by_city.most_common(top_cities)]
    tops = set(top)
    for _, lat, lng, city in pts:
        if city in tops:
            cells[city].add((math.floor(lat / CELL_DEG), math.floor(lng / CELL_DEG)))
            lat_sum[city] += lat
    cities: list[dict] = []
    for c in top:
        # a cell is CELL_DEG square, so its east-west side shrinks with cos(lat)
        k = math.cos(math.radians(lat_sum[c] / by_city[c]))
        cities.append({"city": c, "n": by_city[c], "cells": len(cells[c]),
                       "km2": round(len(cells[c]) * CELL_KM2 * k, 1)})
    cities.sort(key=lambda x: -x["cells"])

    return {"years": years, "thresholds": thresholds, "cities": cities}
