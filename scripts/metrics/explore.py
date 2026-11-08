"""Exploration extras for stats.html (metrics package).

One payload, ``data["explore"]``, eight small views:

* extremes   — northern-, southern-, western-, easternmost check-ins (a check-in into a
               whole country or region is skipped: its point is a centroid, not a place)
* elevation  — highest / lowest check-ins (geo_context.json, Open-Meteo)
* tiles      — distinct zoom-14 map tiles (~2.4 km at the equator, ~1.5 km at
               Minsk), the largest fully visited square and cluster (a tile whose
               four neighbours are all visited), new tiles per year
* regions    — admin-1 regions per country (Nominatim ISO 3166-2 codes per city)
* chains     — brands from config/chains.json: visits, countries, first/last
* capitals   — capitals of the visited countries (config/capitals.json)
* sun        — check-ins after sunset / before sunrise by real solar position,
               white-night (sun never below -9°) and polar days; computed, no API
* bonds      — the longest relationships with a place (first → last visit)
* weather    — the weather of the days you checked in (geo_context.json):
               records, rain/snow/frost shares, home vs away, warmest/coldest trips

Reconstructed (``rf*``) rows sit at day/city granularity, so they only feed the
city-level views (regions, chains, capitals, bonds). Every view degrades to
absent when its input is missing (no geo_context, no config file).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

from transform import group_category

from .trips import _home_at, _localise, _parse_ts

TILE_ZOOM = 14
HOME_CATEGORY = "Home (private)"
SUNSET_DEG = -0.833          # sun's upper limb on the horizon, with refraction
CIVIL_DEG = -6.0
COARSE_CATS = {"Country", "State", "County", "States and Municipalities"}
WHITE_NIGHT_DEG = -9.0       # St Petersburg's white nights: the sun stays above -9°
AREA_GROUP = "Streets & Areas"
# Weather lists keep every day past these (high °C, low °C, rain mm, snow cm),
# at least 10 per list; the page shows 10 and expands to the rest.
WX_FLOORS = {"hot": 30.0, "cold": -10.0, "wet": 20.0, "snow": 3.0}
WX_LIST_CAP = 500
# Temperature Hunter (index page): badges for the day max / night min reached.
HUNT_HEAT = (30, 35, 40, 45, 50)
HUNT_COLD = (-10, -20, -30, -40)
PEAK_STEPS = (500, 1000, 2000, 3000, 4000, 5000)
# Below sea level only from -10 m: Palm Jumeirah or Alexandria's seafront read -9 m
# in the elevation model; Baku (-28 m) or the Dead Sea are the real thing.
BELOW_SEA_M = -10
HIGH_M = 2000             # the heights list keeps every venue from here up
LIST_CAP = 300


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x else None


def _is_rf(r: dict) -> bool:
    return (r.get("checkin_id") or "").startswith("rf")


def _place(r: dict, d: datetime | None = None) -> dict:
    out = {"venue": r.get("venue", ""), "venue_id": r.get("venue_id", ""),
           "city": r.get("city", ""), "country": r.get("country", "")}
    if d is not None:
        out["date"] = d.strftime("%Y-%m-%d")
    return out


def _city_key(r: dict) -> str:
    return f"{r.get('city', '')}|{r.get('country', '')}"


# ── extremes ────────────────────────────────────────────────────────────────

def extremes(pts: list[tuple[dict, datetime, float, float]]) -> dict:
    if not pts:
        return {}
    pick = {"north": max(pts, key=lambda p: p[2]), "south": min(pts, key=lambda p: p[2]),
            "west": min(pts, key=lambda p: p[3]), "east": max(pts, key=lambda p: p[3])}
    return {k: {**_place(r, d), "lat": round(lat, 4), "lng": round(lng, 4)}
            for k, (r, d, lat, lng) in pick.items()}


# ── elevation ───────────────────────────────────────────────────────────────

def elevation(pts: list[tuple[dict, datetime, float, float]], elev: dict,
              pins: set[str] | frozenset = frozenset()) -> dict:
    """Every venue from HIGH_M up (at least 10, the highest), and every venue below
    the sea from BELOW_SEA_M down, one row per venue. `pins`: venue ids pinned on a
    summit only seen from below (config/summit_pins.json) — listed, marked."""
    best: dict[str, tuple[int, dict, datetime]] = {}
    for r, d, lat, lng in pts:
        e = elev.get(f"{lat:.3f},{lng:.3f}")
        if e is None:
            continue
        vid = r.get("venue_id") or f"{lat:.3f},{lng:.3f}"
        if vid not in best:
            best[vid] = (int(e), r, d)
    if not best:
        return {}
    ranked = sorted(best.values(), key=lambda t: t[0])

    def item(e, r, d):
        it = {**_place(r, d), "m": e}
        if r.get("venue_id") in pins:
            it["summit"] = True         # the venue pin is the peak, not where you stood
        return it
    high = [item(e, r, d) for i, (e, r, d) in enumerate(ranked[::-1])
            if (e >= HIGH_M or i < 10) and i < LIST_CAP]
    low = [item(e, r, d) for e, r, d in ranked if e <= BELOW_SEA_M][:LIST_CAP]
    return {"highest": high, "lowest": low, "high_m": HIGH_M, "low_m": BELOW_SEA_M,
            "venues": len(best)}


# ── tiles ───────────────────────────────────────────────────────────────────

def tile_of(lat: float, lng: float, z: int = TILE_ZOOM) -> tuple[int, int]:
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lng + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_centre(x: float, y: float, z: int = TILE_ZOOM) -> tuple[float, float]:
    n = 2 ** z
    lng = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lng


def max_square(tiles: set[tuple[int, int]]) -> tuple[int, tuple[int, int] | None]:
    """Side of the largest fully visited square and its bottom-right tile (DP)."""
    side: dict[tuple[int, int], int] = {}
    best, at = 0, None
    for x, y in sorted(tiles):
        s = 1 + min(side.get((x - 1, y), 0), side.get((x, y - 1), 0), side.get((x - 1, y - 1), 0))
        side[(x, y)] = s
        if s > best:
            best, at = s, (x, y)
    return best, at


def max_cluster(tiles: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Largest 4-connected group of tiles whose four neighbours are all visited."""
    inner = {(x, y) for x, y in tiles
             if {(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)} <= tiles}
    seen: set[tuple[int, int]] = set()
    best: list[tuple[int, int]] = []
    for t in inner:
        if t in seen:
            continue
        group, q = [], deque([t])
        seen.add(t)
        while q:
            x, y = q.popleft()
            group.append((x, y))
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in inner and nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        if len(group) > len(best):
            best = group
    return best


def tiles(pts: list[tuple[dict, datetime, float, float]]) -> dict:
    first: dict[tuple[int, int], int] = {}
    city_of: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for r, d, lat, lng in pts:
        t = tile_of(lat, lng)
        first[t] = min(first.get(t, d.year), d.year)
        city_of[t][r.get("city", "")] += 1
    if not first:
        return {}
    ts = set(first)
    side, br = max_square(ts)
    cl = max_cluster(ts)

    def where(group):
        c = Counter()
        for t in group:
            c.update(city_of.get(t, {}))
        c.pop("", None)
        return c.most_common(1)[0][0] if c else ""
    sq = {}
    if br:
        sq_tiles = [(br[0] - i, br[1] - j) for i in range(side) for j in range(side)]
        lat, lng = tile_centre(br[0] - side / 2 + 1, br[1] - side / 2 + 1)
        sq = {"side": side, "city": where(sq_tiles), "lat": round(lat, 4), "lng": round(lng, 4)}
    by_year = Counter(first.values())
    run, years = 0, []
    for y in sorted(by_year):
        run += by_year[y]
        years.append([y, by_year[y], run])
    return {"total": len(ts), "zoom": TILE_ZOOM, "square": sq,
            "cluster": {"size": len(cl), "city": where(cl)} if cl else {},
            "years": years}


# ── regions ─────────────────────────────────────────────────────────────────

def regions(rows: list[dict], reg: dict) -> dict:
    by_c: dict[str, dict[str, list]] = defaultdict(dict)
    for r in rows:
        hit = reg.get(_city_key(r)) or {}
        code = hit.get("code")
        if not code:
            continue
        slot = by_c[r.get("country", "")].setdefault(code, [hit.get("name") or code, 0])
        slot[1] += 1
    if not by_c:
        return {}
    out: list[dict] = [{"country": c, "n": len(v),
            "regions": sorted(([n, k] for n, k in v.values()), key=lambda t: -t[1])}
           for c, v in by_c.items()]
    out.sort(key=lambda t: (-t["n"], t["country"]))
    return {"total": sum(t["n"] for t in out), "countries": out}


# ── chains ──────────────────────────────────────────────────────────────────

def chains(rows: list[dict], cfg: dict, stamp) -> list[dict]:
    pats = [(b, re.compile(p, re.I)) for b, p in cfg.items() if not b.startswith("_")]
    hits: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    for r in rows:
        name = r.get("venue") or ""
        for b, rx in pats:
            if rx.search(name):
                d = stamp(r)
                if d:
                    hits[b].append((d, r))
                break
    out: list[dict] = []
    for b, lst in hits.items():
        lst.sort(key=lambda t: t[0])
        countries = Counter(r.get("country", "") for _, r in lst)
        out.append({"brand": b, "visits": len(lst), "countries": len(countries),
                    "cities": len({_city_key(r) for _, r in lst}),
                    "country_list": [c for c, _ in countries.most_common()],
                    "first": _place(lst[0][1], lst[0][0]), "last": _place(lst[-1][1], lst[-1][0])})
    out.sort(key=lambda t: (-t["countries"], -t["visits"]))
    return out


# ── capitals ────────────────────────────────────────────────────────────────

def capitals(rows: list[dict], cfg: dict, stamp) -> dict:
    countries = {r.get("country", "") for r in rows} - {""}
    want = {c: cap for c, cap in cfg.items() if c in countries}
    seen: dict[str, list] = {}
    for r in rows:
        c = r.get("country", "")
        if c in want and (r.get("city") or "").casefold() == want[c].casefold():
            d = stamp(r)
            slot = seen.setdefault(c, [d, 0])
            slot[1] += 1
            if d and (slot[0] is None or d < slot[0]):
                slot[0] = d
    visited = sorted(([want[c], c, (d.strftime("%Y-%m-%d") if d else ""), n] for c, (d, n) in seen.items()),
                     key=lambda t: t[2])
    missed = sorted([want[c], c] for c in want if c not in seen)
    return {"visited": visited, "missed": missed, "of": len(want)} if want else {}


# ── sun ─────────────────────────────────────────────────────────────────────

def sun_position(ts: float, lat: float, lng: float) -> tuple[float, float]:
    """(elevation°, declination°) of the sun at unix ts — the low-precision
    almanac formula, good to ~0.1° for these dates, plenty for day/night."""
    d = ts / 86400.0 - 10957.5                       # days since J2000.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians(gmst * 15 + lng) - ra
    phi = math.radians(lat)
    el = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha))
    return math.degrees(el), math.degrees(dec)


def sun(pts: list[tuple[dict, datetime, float, float]]) -> dict:
    by_year: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])   # [day, twilight, night]
    white: dict[str, set] = defaultdict(set)
    polar_day: dict[str, set] = defaultdict(set)
    polar_night: dict[str, set] = defaultdict(set)
    deepest: tuple[float, dict, datetime] | None = None
    for r, d, lat, lng in pts:
        el, dec = sun_position(d.timestamp(), lat, lng)
        k = 0 if el >= SUNSET_DEG else 1 if el >= CIVIL_DEG else 2
        by_year[d.year][k] += 1
        midnight = abs(lat + dec) - 90           # sun height at solar midnight
        noon = 90 - abs(lat - dec)
        day = d.strftime("%Y-%m-%d")
        if midnight > SUNSET_DEG:
            polar_day[r.get("city", "")].add(day)
        elif midnight > WHITE_NIGHT_DEG:
            white[r.get("city", "")].add(day)
        if noon < SUNSET_DEG:
            polar_night[r.get("city", "")].add(day)
        if deepest is None or el < deepest[0]:
            deepest = (el, r, d)
    if not by_year:
        return {}
    tot = [sum(v[i] for v in by_year.values()) for i in range(3)]

    def days(m):
        return sorted(([c, len(v), min(v), max(v)] for c, v in m.items()), key=lambda t: -t[1])
    out = {"day": tot[0], "twilight": tot[1], "night": tot[2],
           "years": [[y, *by_year[y]] for y in sorted(by_year)],
           "white_nights": days(white), "polar_day": days(polar_day), "polar_night": days(polar_night)}
    if deepest:
        out["deepest"] = {**_place(deepest[1], deepest[2]), "deg": round(deepest[0], 1),
                          "time": deepest[2].strftime("%H:%M")}
    return out


# ── bonds ───────────────────────────────────────────────────────────────────

def bonds(rows: list[dict], stamp, skip: set[str] | frozenset = frozenset(), top: int = 10,
          min_visits: int = 3) -> list[dict]:
    """Places, not homes or areas: `skip` holds the grouped categories to leave out."""
    per: dict[str, list] = {}
    for r in rows:
        vid = r.get("venue_id") or ""
        if not vid or group_category(r) in skip:
            continue
        d = stamp(r)
        if not d:
            continue
        p = per.setdefault(vid, [d, d, 0, set(), r])
        p[0], p[1] = min(p[0], d), max(p[1], d)
        p[2] += 1
        p[3].add(d.year)
    out = [{**_place(r), "first": a.strftime("%Y-%m-%d"), "last": b.strftime("%Y-%m-%d"),
            "years": round((b - a).days / 365.25, 1), "visits": n, "active_years": len(ys)}
           for a, b, n, ys, r in per.values() if n >= min_visits]
    out.sort(key=lambda t: (-t["years"], -t["visits"]))
    return out[:top]


# ── weather ─────────────────────────────────────────────────────────────────

def weather(pts: list[tuple[dict, datetime, float, float]], wx: dict, trips: list[dict] | None,
            home_city: str, home_history) -> dict:
    days: dict[tuple[str, str], tuple[list, dict, bool]] = {}
    for r, d, _lat, _lng in pts:
        k = _city_key(r)
        day = d.strftime("%Y-%m-%d")
        if (k, day) in days:
            continue
        w = (wx.get(k) or {}).get(day)
        if not w or w[0] is None or w[1] is None:
            continue
        home = (r.get("city") or "") == _home_at(int(r["date"]), home_city, home_history)
        days[(k, day)] = (w, r, home)
    if not days:
        return {}
    vals = list(days.items())

    def top(key, tie, floor: float, keep=lambda w: True, n_min: int = 10,
            cap: int = WX_LIST_CAP) -> list[dict]:
        """Every day whose key reaches `floor` (largest first; at least n_min days
        whatever the floor, at most cap), one per date: three rainy
        stops on one road on one day are one rainy day, told by its most extreme
        place; other days keep their own rows, the same city included (a heatwave's
        second day, Minsk's hottest days of different years). Places in one ERA5 cell
        tie; `tie` (harsher weather) picks the one to show — the pass over the village."""
        main: dict[str, tuple] = {}
        for kv in vals:
            (k, day), (w, _r, _h) = kv
            if not keep(w):
                continue
            rank = (key(w), tie(w))
            if day not in main or rank > main[day][0]:
                main[day] = (rank, kv)
        out = []
        for (k, day), (w, r, _h) in sorted((kv for _, kv in main.values()),
                                           key=lambda kv: key(kv[1][0]), reverse=True):
            if len(out) >= cap or (len(out) >= n_min and key(w) < floor):
                break
            out.append({"city": r.get("city", ""), "country": r.get("country", ""), "date": day,
                        "tmax": w[0], "tmin": w[1], "precip": w[2], "snow": w[3], "code": w[4]})
        return out

    def share(sel):
        n = len(sel) or 1
        return {"n": len(sel),
                "tmax": round(sum(w[0] for w, _, _ in sel) / n, 1),
                "rain": round(100 * sum(1 for w, _, _ in sel if (w[2] or 0) >= 1) / n),
                "snow": round(100 * sum(1 for w, _, _ in sel if (w[3] or 0) > 0) / n),
                "frost": round(100 * sum(1 for w, _, _ in sel if w[0] < 0) / n),
                "hot": round(100 * sum(1 for w, _, _ in sel if w[0] >= 30) / n)}
    allv = [v for _, v in vals]
    out = {"days": len(days),
           "hottest": top(lambda w: w[0], lambda w: w[1], WX_FLOORS["hot"]),
           "coldest": top(lambda w: -w[1], lambda w: -w[0], -WX_FLOORS["cold"]),
           "wettest": top(lambda w: w[2] or 0, lambda w: -w[0], WX_FLOORS["wet"],
                          keep=lambda w: (w[2] or 0) > 0),
           "snowiest": top(lambda w: w[3] or 0, lambda w: -w[0], WX_FLOORS["snow"],
                           keep=lambda w: (w[3] or 0) > 0),
           "floors": WX_FLOORS,
           "all": share(allv),
           "home": share([v for v in allv if v[2]]),
           "away": share([v for v in allv if not v[2]])}
    out["hunter"] = hunter(vals)
    tw = []
    for t in trips or []:
        seen, temps = set(), []
        for c in t.get("checkins") or []:
            k, day = f"{c.get('city', '')}|{c.get('country', '')}", c.get("date", "")
            w = (wx.get(k) or {}).get(day)
            if w and (k, day) not in seen and w[0] is not None:
                seen.add((k, day))
                temps.append(w[0])
        if len(temps) >= 2:
            tw.append({"name": t.get("name", ""), "start": t.get("start_date", ""),
                       "tmax": round(sum(temps) / len(temps), 1), "days": len(temps)})
    if tw:
        tw.sort(key=lambda t: t["tmax"])
        out["trips_coldest"] = tw[:5]
        out["trips_warmest"] = tw[::-1][:5]
    return out


def _streak(dates: list[str]) -> tuple[int, str, str]:
    """Longest run of consecutive calendar days in `dates` → (length, first, last)."""
    from datetime import date as _d
    best, cur, start, bs, be, prev = 0, 0, "", "", "", None
    for s in sorted(set(dates)):
        d = _d.fromisoformat(s)
        cur = cur + 1 if prev is not None and (d - prev).days == 1 else 1
        if cur == 1:
            start = s
        if cur > best:
            best, bs, be = cur, start, s
        prev = d
    return best, bs, be


def hunter(vals: list) -> dict:
    """Temperature Hunter: the hottest day max and coldest night min reached, each
    badge with the first day it was reached, and the longest hot / ice streaks.
    `vals` = [((city key, day), (w, row, home))] as built by weather()."""
    def place(day, w, r, val):
        return {"city": r.get("city", ""), "country": r.get("country", ""), "date": day, "t": val}
    by_date = sorted(vals, key=lambda kv: kv[0][1])
    hot = max(vals, key=lambda kv: kv[1][0][0])
    cold = min(vals, key=lambda kv: kv[1][0][1])

    def badges(steps, reached):
        out = []
        for s in steps:
            first = next(((day, w, r) for (k, day), (w, r, _h) in by_date if reached(w, s)), None)
            n = len({day for (k, day), (w, _r, _h) in vals if reached(w, s)})
            out.append({"t": s, "days": n, **({"first": place(first[0], first[1], first[2], s)} if first else {})})
        return out
    hs = _streak([day for (k, day), (w, _r, _h) in vals if w[0] >= 30])
    ice = _streak([day for (k, day), (w, _r, _h) in vals if w[0] < 0])
    return {
        "heat": {"best": place(hot[0][1], hot[1][0], hot[1][1], hot[1][0][0]), "goal": HUNT_HEAT[-1],
                 "badges": badges(HUNT_HEAT, lambda w, s: w[0] >= s),
                 "streak": {"days": hs[0], "from": hs[1], "to": hs[2]}},
        "cold": {"best": place(cold[0][1], cold[1][0], cold[1][1], cold[1][0][1]), "goal": HUNT_COLD[-1],
                 "badges": badges(HUNT_COLD, lambda w, s: w[1] <= s),
                 "streak": {"days": ice[0], "from": ice[1], "to": ice[2]}},
    }


# ── peaks (index Peak Hunter) ───────────────────────────────────────────────

def continent_of(country: str, lat: float, lng: float, cmap: dict) -> str:
    c = cmap.get(country, "")
    if country == "Russia" and lng >= 60:
        return "Asia"                                 # east of the Urals
    if country == "Turkey" and lng < 29.05 and lat > 40.9:
        return "Europe"                               # Istanbul's European bank, Thrace
    return c


def peaks(pts: list[tuple[dict, datetime, float, float]], elev: dict, cfg: dict,
          pins: set[str] | frozenset = frozenset()) -> dict:
    """Highest check-in overall and per continent, height badges with the first
    day reached, and land below sea level. Summit pins (a mountain venue pinned on
    its peak) never count as reached; the top few are listed apart."""
    cmap = cfg.get("countries") or {}
    seen = []
    pinned: dict[str, dict] = {}
    for r, d, lat, lng in pts:
        e = elev.get(f"{lat:.3f},{lng:.3f}")
        if e is None:
            continue
        item = {**_place(r, d), "m": int(e), "continent": continent_of(r.get("country", ""), lat, lng, cmap)}
        if r.get("venue_id") in pins:
            vid = r.get("venue_id") or item["venue"]
            if vid not in pinned or pinned[vid]["m"] < item["m"]:
                pinned[vid] = item
            continue
        seen.append((d, item))
    if not seen:
        return {}
    seen.sort(key=lambda t: t[0])
    best = max((it for _, it in seen), key=lambda it: it["m"])
    badges = []
    for s in PEAK_STEPS:
        first = next((it for _, it in seen if it["m"] >= s), None)
        badges.append({"m": s, **({"first": first} if first else {})})
    conts: dict[str, dict] = {}
    for _, it in seen:
        c = it["continent"]
        if not c:
            continue
        slot = conts.setdefault(c, {"continent": c, "high": it, "low": it})
        if it["m"] > slot["high"]["m"]:
            slot["high"] = it
        if it["m"] < slot["low"]["m"]:
            slot["low"] = it
    top, lows = cfg.get("peaks") or {}, cfg.get("lows") or {}
    rows = []
    for c, v in conts.items():
        row = {"continent": c, "high": v["high"], "peak": top.get(c)}
        if v["low"]["m"] <= BELOW_SEA_M:           # real depressions; a few metres is coastal DEM noise
            row["low"] = v["low"]
            row["deepest"] = lows.get(c)
        rows.append(row)
    rows.sort(key=lambda t: -t["high"]["m"])
    return {"best": best, "goal": max((p[1] for p in top.values()), default=8849), "badges": badges,
            "continents": rows,
            "summit_pins": sorted(pinned.values(), key=lambda it: -it["m"])[:3]}


# ── entry point ─────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _skip_cats(cfg: Path) -> set[str]:
    groups = _load(cfg / "categories.json").get("category_groups") or {}
    return set(groups.get(AREA_GROUP) or []) | {HOME_CATEGORY}


def compute_explore(rows: list[dict], config_dir: Path | str, geo: dict | None = None,
                    trips: list[dict] | None = None, home_city: str = "",
                    home_history=None, summit_pins: set[str] | None = None) -> dict:
    cfg = Path(config_dir)
    geo = geo or {}
    local: dict[int, datetime | None] = {}

    def stamp(r: dict) -> datetime | None:
        """Local datetime (rf rows: their UTC date is the day, which is all they carry)."""
        key = id(r)
        if key not in local:
            d = _parse_ts(r)
            lat, lng = _f(r.get("lat")), _f(r.get("lng"))
            local[key] = (_localise(d, lat, lng, r.get("country", "")) if d and lat is not None
                          and lng is not None and not _is_rf(r) else d)
        return local[key]

    pts = []
    for r in rows:
        if _is_rf(r) or group_category(r) in COARSE_CATS:
            continue                 # day/city-level rows; a check-in into a whole country
        lat, lng = _f(r.get("lat")), _f(r.get("lng"))
        d = stamp(r)
        if lat is None or lng is None or d is None:
            continue
        pts.append((r, d, lat, lng))

    out: dict = {"extremes": extremes(pts), "tiles": tiles(pts), "sun": sun(pts),
                 "bonds": bonds(rows, stamp, _skip_cats(cfg))}
    if geo.get("elevation"):
        pins = summit_pins if summit_pins is not None else {
            k for k in _load(cfg / "summit_pins.json") if not k.startswith("_")}
        out["elevation"] = elevation(pts, geo["elevation"], pins)
        out["peaks"] = peaks(pts, geo["elevation"], _load(cfg / "continents.json"), pins)
    if geo.get("regions"):
        out["regions"] = regions(rows, geo["regions"])
    if geo.get("weather"):
        out["weather"] = weather(pts, geo["weather"], trips, home_city, home_history)
    ch = _load(cfg / "chains.json")
    if ch:
        out["chains"] = chains(rows, ch, stamp)
    caps = _load(cfg / "capitals.json")
    if caps:
        out["capitals"] = capitals(rows, caps, stamp)
    return {k: v for k, v in out.items() if v}
