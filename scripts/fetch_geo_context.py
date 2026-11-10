#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_geo_context.py — cache elevation, weather and admin regions for the stats.

Three free services, each asked once per thing and kept in the data repo's
geo_context.json, so the build never waits on the network:

  elevation  {"53.904,27.561": 221}            OpenTopoData (ASTER 30 m), 100 points per
                                               call, 1 call/s, coordinates rounded to 3
                                               decimals (~100 m)
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
        --cache $DATA/geo_context.json [--max-elevation 300] [--max-weather 600] [--max-regions 900]
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

# OpenTopoData public API, ASTER 30 m: land of 83°N–83°S, 0 over the sea (no seabed
# depths to pass for "below sea level"). 100 points a call, 1 call/s, 1,000 calls/day.
# Open-Meteo's elevation API counted every point against 5,000/hour and timed out
# from GitHub runners altogether.
ELEV_URL = "https://api.opentopodata.org/v1/aster30m"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
UA = "4sq-dashboard geo-context (+https://github.com/toouur/foursquare-dashboard)"
DAILY = "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,weather_code"
ARCHIVE_LAG_DAYS = 7          # ERA5 trails real time by ~5 days
RANGE_GAP_DAYS = 20           # merge needed days closer than this into one request
RETRY_DAYS = 90               # a region lookup that found nothing is retried after this
MOVED_DEG = 0.02              # a city whose median point moved this far is asked again
ELEV_PAUSE = 1.1              # OpenTopoData: at most one call a second


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


# A check-in into a whole country/state carries the country's centroid (Minsk for
# Belarus, Kyiv for Ukraine), whatever city the trip context gave it: no point, no day.
COARSE_CATS = {"Country", "State", "County", "States and Municipalities"}


def needs(rows: list[dict]) -> tuple[set[str], dict[str, tuple[float, float]], dict[str, set[date]]]:
    """(coord keys, city median points, city → local days with a check-in)."""
    from transform import group_category
    coords: set[str] = set()
    pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    days: dict[str, set[date]] = defaultdict(set)
    for r in _real(rows):
        if group_category(r) in COARSE_CATS:
            continue
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


def _fetch(session, url: str, tries: int = 3, **kw) -> tuple[int, dict]:
    """(HTTP status, JSON body). A timeout, connection error or 5xx is retried
    with a growing pause; after `tries` it is (0, {}), so a flaky service ends
    that service's run instead of the whole script."""
    for attempt in range(tries):
        try:
            r = session.get(url, **kw)
        except requests.RequestException as exc:
            print(f"{url}: {exc}", file=sys.stderr)
            r = None
        if r is not None and r.status_code < 500:
            break
        if attempt < tries - 1:
            time.sleep(15 * (attempt + 1))
    else:
        return 0, {}
    if r.status_code != 200:
        return r.status_code, {}
    try:
        return 200, r.json() or {}
    except ValueError:
        return 0, {}


def fill_elevation(cache: dict, coords: set[str], budget: int, session=requests) -> int:
    todo = sorted(c for c in coords if c not in cache)
    calls = 0
    for i in range(0, len(todo), 100):
        if calls >= budget:
            break
        batch = todo[i:i + 100]
        status, body = _fetch(session, ELEV_URL, params={"locations": "|".join(batch)}, timeout=60)
        calls += 1
        if status != 200:
            print(f"elevation: {status or 'network error'} — stopping", file=sys.stderr)
            break
        for c, hit in zip(batch, body.get("results") or []):
            e = (hit or {}).get("elevation")
            if e is not None:
                cache[c] = round(e)
        time.sleep(ELEV_PAUSE)
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
            status, body = _fetch(session, ARCHIVE_URL, params={
                "latitude": round(lat, 3), "longitude": round(lng, 3),
                "start_date": a.isoformat(), "end_date": b.isoformat(),
                "daily": DAILY, "timezone": "auto"}, timeout=60)
            calls += 1
            if status in (0, 429):
                print(f"weather: {status or 'network error'} — stopping for today", file=sys.stderr)
                return calls
            if status != 200:
                print(f"weather {k} {a}..{b}: HTTP {status}", file=sys.stderr)
                continue
            dly = body.get("daily") or {}
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


REGION_LEVELS = ("ISO3166-2-lvl4", "ISO3166-2-lvl3", "ISO3166-2-lvl5", "ISO3166-2-lvl6",
                 # Small countries keep their ISO subdivision on a municipal level:
                 # Slovenia, Malta, Liechtenstein (lvl8), North Macedonia (lvl7).
                 "ISO3166-2-lvl7", "ISO3166-2-lvl8")
REGION_ZOOMS = (8, 10)        # 8 = state level; 10 reaches the municipal codes above


def _region(addr: dict) -> tuple[str | None, str]:
    code = next((addr[k] for k in REGION_LEVELS if addr.get(k)), None)
    name = next((addr[k] for k in ("state", "region", "province", "county", "municipality") if addr.get(k)), "")
    return code, name


def fill_regions(cache: dict, centre: dict, budget: int, now: float, session=requests) -> int:
    calls = 0
    for k, (lat, lng) in sorted(centre.items()):
        hit = cache.get(k)
        at = (hit or {}).get("at")
        moved = not at or abs(at[0] - lat) > MOVED_DEG or abs(at[1] - lng) > MOVED_DEG
        if hit and not moved and (hit.get("code") or now - hit.get("t", 0) < RETRY_DAYS * 86400):
            continue
        if calls >= budget:
            break
        code, name, status = None, "", 200
        for zoom in REGION_ZOOMS:
            status, body = _fetch(session, REVERSE_URL, params={
                "lat": lat, "lon": lng, "format": "jsonv2", "zoom": zoom,
                "addressdetails": 1, "accept-language": "en"}, headers={"User-Agent": UA}, timeout=30)
            calls += 1
            time.sleep(1.1)
            if status != 200:
                break
            code, name = _region(body.get("address") or {})
            if code:
                break
        if status != 200:
            print(f"region {k}: {status or 'network error'}", file=sys.stderr)
            if status in (0, 403, 429):
                break
            continue
        cache[k] = {"code": code, "name": name, "t": int(now), "at": [round(lat, 3), round(lng, 3)]}
    return calls


# ── region outlines (the regions map) ──────────────────────────────────────

SHAPE_ZOOMS = (8, 5, 6, 10, 7, 9, 4)   # whichever level carries the code's own boundary
SHAPE_THRESHOLD = 0.005                 # Nominatim simplification, ~500 m
SHAPE_DIGITS = 3


def _round(c, nd: int = SHAPE_DIGITS):
    return round(c, nd) if isinstance(c, (int, float)) else [_round(x, nd) for x in c]


def region_points(reg: dict, centre: dict, counts: dict, config_dir: Path) -> dict[str, tuple[float, float]]:
    """ISO code → the median point of its busiest city, codes resolved exactly as
    the stats card resolves them (fixes, border drop, parent climb)."""
    from metrics.explore import _load, region_code
    iso = _load(config_dir / "regions_iso.json")
    fixes = {k: v for k, v in _load(config_dir / "region_fixes.json").items() if not k.startswith("_")}
    flags = _load(config_dir / "country_flags.json")
    best: dict[str, tuple[int, tuple[float, float]]] = {}
    for k, pt in centre.items():
        code = region_code(k, flags.get(k.split("|", 1)[1], ""), reg, iso, fixes)
        if code and counts.get(k, 0) > best.get(code, (-1, pt))[0]:
            best[code] = (counts.get(k, 0), pt)
    return {c: v[1] for c, v in best.items()}


def _is_region(body: dict, code: str) -> bool:
    """The reverse hit IS the region `code`, not a city or county inside it: its own
    ISO3166-2 tag says so, or its admin_level is the level at which the address
    carries the code (Minsk: a place node merged into the level-4 boundary carries
    no ISO tag of its own, only admin_level 4 next to ISO3166-2-lvl4 = BY-HM)."""
    tags = body.get("extratags") or {}
    if tags.get("ISO3166-2") == code:
        return True
    addr = body.get("address") or {}
    levels = {k.rsplit("lvl", 1)[1] for k, v in addr.items() if k.startswith("ISO3166-2-lvl") and v == code}
    return str(tags.get("admin_level") or "") in levels


def fill_region_shapes(shapes: dict, points: dict, budget: int, now: float, session=requests) -> int:
    """{code: {"g": GeoJSON geometry | None, "t": ts}} — Nominatim reverse at the
    region's point, walking zoom levels until the hit's own ISO3166-2 tag is the
    code (so a county or a city is never stored as the region)."""
    calls = 0
    for code, (lat, lng) in sorted(points.items()):
        hit = shapes.get(code)
        if hit and (hit.get("g") or now - hit.get("t", 0) < RETRY_DAYS * 86400):
            continue
        if calls >= budget:
            break
        got, status = None, 200
        for zoom in SHAPE_ZOOMS:
            status, body = _fetch(session, REVERSE_URL, params={
                "lat": lat, "lon": lng, "format": "jsonv2", "zoom": zoom, "extratags": 1, "addressdetails": 1,
                "polygon_geojson": 1, "polygon_threshold": SHAPE_THRESHOLD}, headers={"User-Agent": UA}, timeout=60)
            calls += 1
            time.sleep(1.1)
            if status != 200:
                break
            if _is_region(body, code):
                g = body.get("geojson") or {}
                if "Polygon" in (g.get("type") or ""):
                    got = {"type": g["type"], "coordinates": _round(g["coordinates"])}
                break
        if status != 200:
            print(f"shape {code}: {status or 'network error'}", file=sys.stderr)
            if status in (0, 403, 429):
                break
            continue
        shapes[code] = {"g": got, "t": int(now)}
    return calls


# ── climate normals (weather luck) ─────────────────────────────────────────

NORMAL_YEARS = range(2011, 2021)      # ERA5, the same source as the visit days
NORMAL_MIN_DAYS = 4                   # a city needs this many weather days to be judged


def fill_normals(cache: dict, wx: dict, centre: dict, budget: int, session=requests) -> int:
    """{"City|Country": {"07": [mean tmax, mean precip mm, share of days >= 1 mm, n]}}
    for the calendar months a city was visited in, from the same point and the
    same archive as the visit days; one request per month and year."""
    calls = 0
    for k in sorted(wx, key=lambda c: -len(wx[c])):
        if len(wx[k]) < NORMAL_MIN_DAYS or k not in centre:
            continue
        have = cache.setdefault(k, {})
        for m in sorted({d[5:7] for d in wx[k]}):
            if m in have:
                continue
            if calls + len(NORMAL_YEARS) > budget:
                if not have:
                    cache.pop(k, None)
                return calls
            lat, lng = centre[k]
            vals: list[tuple[float, float]] = []
            for y in NORMAL_YEARS:
                a = date(y, int(m), 1)
                b = (a + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                status, body = _fetch(session, ARCHIVE_URL, params={
                    "latitude": round(lat, 3), "longitude": round(lng, 3), "start_date": a.isoformat(),
                    "end_date": b.isoformat(), "daily": "temperature_2m_max,precipitation_sum",
                    "timezone": "auto"}, timeout=60)
                calls += 1
                if status in (0, 429):
                    print(f"normals: {status or 'network error'} — stopping for today", file=sys.stderr)
                    if not have:
                        cache.pop(k, None)
                    return calls
                dly = body.get("daily") or {}
                vals += [(tx, p) for tx, p in zip(dly.get("temperature_2m_max") or [],
                                                  dly.get("precipitation_sum") or [])
                         if tx is not None and p is not None]
                time.sleep(0.3)
            if vals:
                have[m] = [round(sum(v[0] for v in vals) / len(vals), 1),
                           round(sum(v[1] for v in vals) / len(vals), 2),
                           round(sum(1 for v in vals if v[1] >= 1) / len(vals), 3), len(vals)]
        if not have:
            cache.pop(k, None)
    return calls


# ── public holidays (Nager.Date) ───────────────────────────────────────────

HOLIDAY_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}"


def holiday_pairs(rows: list[dict], flags: dict) -> set[str]:
    """"CC-YYYY" for every country and year with a check-in (UTC year is enough:
    a holiday on Dec 31 / Jan 1 sits in the year it is dated)."""
    out = set()
    for r in _real(rows):
        cc = (flags.get(r.get("country", "")) or "").upper()
        if cc and r.get("date"):
            y = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            out.add(f"{cc}-{y}")
    return out


def fill_holidays(cache: dict, pairs: set[str], budget: int, session=requests) -> int:
    """{"MD-2023": [["2023-01-01", "New Year's Day"], ...]} — nationwide holidays only;
    a country Nager.Date does not cover is stored as [] (204/404)."""
    calls = 0
    for key in sorted(pairs):
        if key in cache:
            continue
        if calls >= budget:
            break
        cc, y = key.split("-")
        status, body = _fetch(session, HOLIDAY_URL.format(year=y, cc=cc), headers={"User-Agent": UA}, timeout=30)
        calls += 1
        time.sleep(0.2)
        if status == 0:
            print("holidays: network error — stopping", file=sys.stderr)
            break
        items = body if isinstance(body, list) else []
        cache[key] = [[h["date"], h.get("name") or h.get("localName") or ""] for h in items
                      if h.get("global", True) and h.get("date")]
    return calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--cache", required=True)
    ap.add_argument("--max-elevation", type=int, default=300,
                    help="elevation calls (100 points each, 1/s; OpenTopoData allows 1,000 a day)")
    ap.add_argument("--max-weather", type=int, default=600, help="archive range requests")
    ap.add_argument("--max-regions", type=int, default=900, help="Nominatim lookups (1/s)")
    ap.add_argument("--max-shapes", type=int, default=500, help="Nominatim outline lookups (1/s)")
    ap.add_argument("--max-holidays", type=int, default=300, help="Nager.Date country-year lookups")
    ap.add_argument("--max-normals", type=int, default=400,
                    help="archive requests for climate normals (10 per city-month)")
    args = ap.parse_args()

    rows = load_rows(Path(args.csv), Path(args.config_dir))
    path = Path(args.cache)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for part in ("elevation", "weather", "regions", "region_shapes", "normals", "holidays"):
        cache.setdefault(part, {})

    def save() -> None:
        path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
                        encoding="utf-8")

    coords, centre, days = needs(rows)
    now = datetime.now(timezone.utc)
    # Saved after every service, so a failure in a later one keeps what came before.
    ne = fill_elevation(cache["elevation"], coords, args.max_elevation)
    save()
    nw = fill_weather(cache["weather"], centre, days, args.max_weather, now.date())
    save()
    nr = fill_regions(cache["regions"], centre, args.max_regions, now.timestamp())
    save()
    from collections import Counter
    from transform import group_category
    counts = Counter(city_key(r["city"], r.get("country", "")) for r in _real(rows)
                     if group_category(r) not in COARSE_CATS)
    points = region_points(cache["regions"], centre, counts, Path(args.config_dir))
    ns = fill_region_shapes(cache["region_shapes"], points, args.max_shapes, now.timestamp())
    save()
    nn = fill_normals(cache["normals"], cache["weather"], centre, args.max_normals)
    save()
    from metrics.explore import _load
    nh = fill_holidays(cache["holidays"], holiday_pairs(rows, _load(Path(args.config_dir) / "country_flags.json")),
                       args.max_holidays)
    save()
    have_days = sum(len(v) for v in cache["weather"].values())
    need_days = sum(len(v) for v in days.values())
    print(f"elevation {len(cache['elevation'])}/{len(coords)} points ({ne} calls) · "
          f"weather {have_days}/{need_days} city-days ({nw} calls) · "
          f"regions {sum(1 for v in cache['regions'].values() if v.get('code'))}/{len(centre)} cities ({nr} calls) · "
          f"outlines {sum(1 for c in points if (cache['region_shapes'].get(c) or {}).get('g'))}/{len(points)} ({ns} calls) · "
          f"normals {sum(len(v) for v in cache['normals'].values())} city-months ({nn} calls) · "
          f"holidays {len(cache['holidays'])} country-years ({nh} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
