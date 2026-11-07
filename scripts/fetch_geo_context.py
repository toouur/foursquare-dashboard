#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_geo_context.py — cache elevation, weather and admin regions for the stats.

Three free services, each asked once per thing and kept in the data repo's
geo_context.json, so the build never waits on the network:

  elevation  {"53.904,27.561": 221}            Open-Meteo elevation, 100 points per call,
                                               coordinates rounded to 3 decimals (~100 m)
  weather    {"Minsk|Belarus": {"2024-07-21": [tmax, tmin, precip_mm, snow_cm, wmo]}}
                                               Open-Meteo archive (ERA5), only the LOCAL days
                                               that have a check-in there; contiguous days
                                               are fetched in one range
  regions    {"Minsk|Belarus": {"code": "BY-MI", "name": "Minsk Region", "t": ts}}
                                               Nominatim reverse at the city's median point,
                                               1 request/s (usage policy); code null = none

Run from warm-routes.yml (nightly, commits the cache); --max-* caps each service
per run so a first fill drains over a few nights. build.py --geo-context reads it.

    python scripts/fetch_geo_context.py --csv $DATA/checkins.csv --config-dir config \\
        --cache $DATA/geo_context.json [--max-elevation 400] [--max-weather 600] [--max-regions 900]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

ELEV_URL = "https://api.open-meteo.com/v1/elevation"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
UA = "4sq-dashboard geo-context (+https://github.com/toouur/foursquare-dashboard)"
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,weather_code"
ARCHIVE_LAG_DAYS = 7          # ERA5 trails real time by ~5 days
RANGE_GAP_DAYS = 20           # merge needed days closer than this into one request
RETRY_DAYS = 90               # a region lookup that found nothing is retried after this


def coord_key(lat: float, lng: float) -> str:
    return f"{lat:.3f},{lng:.3f}"


def city_key(city: str, country: str) -> str:
    return f"{city}|{country}"


def _real(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not (r.get("checkin_id") or "").startswith("rf")
            and r.get("lat") and r.get("lng") and r.get("city")]


def load_rows(csv_path: Path, config_dir: Path) -> list[dict]:
    """checkins.csv (+ backfill.csv beside it) through the same transforms as the build."""
    from transform import apply_transforms, build_blank_city_resolver, load_mappings
    rows: list[dict] = []
    for p in (csv_path, csv_path.resolve().parent / "backfill.csv"):
        if p.exists():
            with open(p, encoding="utf-8-sig") as fh:
                rows.extend(csv.DictReader(fh))
    rows.sort(key=lambda r: int(r.get("date") or 0))
    resolver = build_blank_city_resolver(config_dir / "city_merge_normalized_review.csv")
    return apply_transforms(rows, load_mappings(config_dir), blank_city_resolver=resolver)


def local_day(r: dict) -> date | None:
    from metrics.trips import _localise, _parse_ts
    d = _parse_ts(r)
    if d is None:
        return None
    return _localise(d, float(r["lat"]), float(r["lng"]), r.get("country", "")).date()


def needs(rows: list[dict]) -> tuple[set[str], dict[str, tuple[float, float]], dict[str, set[date]]]:
    """(coord keys, city median points, city → local days with a check-in)."""
    coords: set[str] = set()
    pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    days: dict[str, set[date]] = defaultdict(set)
    for r in _real(rows):
        lat, lng = float(r["lat"]), float(r["lng"])
        coords.add(coord_key(lat, lng))
        k = city_key(r["city"], r.get("country", ""))
        pts[k].append((lat, lng))
        d = local_day(r)
        if d:
            days[k].add(d)
    centre = {k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
              for k, v in pts.items()}
    return coords, centre, days


def ranges(days: list[date], gap: int = RANGE_GAP_DAYS) -> list[tuple[date, date]]:
    """Sorted days → [(start, end)], joining runs whose gap is under `gap` days."""
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and (d - out[-1][1]).days <= gap:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def fill_elevation(cache: dict, coords: set[str], budget: int, session=requests) -> int:
    todo = sorted(c for c in coords if c not in cache)
    calls = 0
    for i in range(0, len(todo), 100):
        if calls >= budget:
            break
        batch = todo[i:i + 100]
        lats = ",".join(c.split(",")[0] for c in batch)
        lngs = ",".join(c.split(",")[1] for c in batch)
        r = session.get(ELEV_URL, params={"latitude": lats, "longitude": lngs}, timeout=60)
        calls += 1
        if r.status_code != 200:
            print(f"elevation: HTTP {r.status_code} — stopping", file=sys.stderr)
            break
        for c, e in zip(batch, r.json().get("elevation") or []):
            if e is not None:
                cache[c] = round(e)
        time.sleep(0.2)
    return calls


def fill_weather(cache: dict, centre: dict, days: dict, budget: int, today: date,
                 session=requests) -> int:
    newest = today - timedelta(days=ARCHIVE_LAG_DAYS)
    calls = 0
    # Biggest gaps first: a city page with no weather at all beats a missing day.
    order = sorted(days, key=lambda k: -len(days[k] - {date.fromisoformat(x) for x in cache.get(k, {})}))
    for k in order:
        have = cache.setdefault(k, {})
        missing = [d for d in days[k] if d.isoformat() not in have and d <= newest]
        for a, b in ranges(missing):
            if calls >= budget:
                return calls
            lat, lng = centre[k]
            r = session.get(ARCHIVE_URL, params={
                "latitude": round(lat, 3), "longitude": round(lng, 3),
                "start_date": a.isoformat(), "end_date": b.isoformat(),
                "daily": DAILY, "timezone": "auto"}, timeout=60)
            calls += 1
            if r.status_code == 429:
                print("weather: rate limited — stopping for today", file=sys.stderr)
                return calls
            if r.status_code != 200:
                print(f"weather {k} {a}..{b}: HTTP {r.status_code}", file=sys.stderr)
                continue
            dly = r.json().get("daily") or {}
            want = {d.isoformat() for d in missing}
            for i, day in enumerate(dly.get("time") or []):
                if day not in want:
                    continue
                vals = [dly.get(f, [None] * (i + 1))[i] for f in DAILY.split(",")]
                if vals[0] is None:
                    continue
                have[day] = [round(v, 1) if isinstance(v, float) else v for v in vals]
            time.sleep(0.3)
        if not have:
            cache.pop(k, None)
    return calls


def _region(addr: dict) -> tuple[str | None, str]:
    code = next((addr[k] for k in ("ISO3166-2-lvl4", "ISO3166-2-lvl3", "ISO3166-2-lvl5", "ISO3166-2-lvl6")
                 if addr.get(k)), None)
    name = next((addr[k] for k in ("state", "region", "province", "county") if addr.get(k)), "")
    return code, name


def fill_regions(cache: dict, centre: dict, budget: int, now: float, session=requests) -> int:
    calls = 0
    for k, (lat, lng) in sorted(centre.items()):
        hit = cache.get(k)
        if hit and (hit.get("code") or now - hit.get("t", 0) < RETRY_DAYS * 86400):
            continue
        if calls >= budget:
            break
        r = session.get(REVERSE_URL, params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 8,
                                             "addressdetails": 1, "accept-language": "en"},
                        headers={"User-Agent": UA}, timeout=30)
        calls += 1
        time.sleep(1.1)
        if r.status_code != 200:
            print(f"region {k}: HTTP {r.status_code}", file=sys.stderr)
            if r.status_code in (403, 429):
                break
            continue
        code, name = _region((r.json() or {}).get("address") or {})
        cache[k] = {"code": code, "name": name, "t": int(now)}
    return calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--cache", required=True)
    ap.add_argument("--max-elevation", type=int, default=400, help="elevation calls (100 points each)")
    ap.add_argument("--max-weather", type=int, default=600, help="archive range requests")
    ap.add_argument("--max-regions", type=int, default=900, help="Nominatim lookups (1/s)")
    args = ap.parse_args()

    rows = load_rows(Path(args.csv), Path(args.config_dir))
    path = Path(args.cache)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for part in ("elevation", "weather", "regions"):
        cache.setdefault(part, {})

    coords, centre, days = needs(rows)
    now = datetime.now(timezone.utc)
    ne = fill_elevation(cache["elevation"], coords, args.max_elevation)
    nw = fill_weather(cache["weather"], centre, days, args.max_weather, now.date())
    nr = fill_regions(cache["regions"], centre, args.max_regions, now.timestamp())

    path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
                    encoding="utf-8")
    have_days = sum(len(v) for v in cache["weather"].values())
    need_days = sum(len(v) for v in days.values())
    print(f"elevation {len(cache['elevation'])}/{len(coords)} points ({ne} calls) · "
          f"weather {have_days}/{need_days} city-days ({nw} calls) · "
          f"regions {sum(1 for v in cache['regions'].values() if v.get('code'))}/{len(centre)} cities ({nr} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
