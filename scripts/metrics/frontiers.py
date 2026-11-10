# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""frontiers.py — borders, time zones, streaks, weather luck and the regions still
missing. Every function takes the same `pts` as metrics.explore: (row, LOCAL
datetime, lat, lng) for real place check-ins (no rf rows, no whole-country rows),
in time order.

  borders   land/sea crossings between consecutive check-ins in two countries
            (not a flight: no airport on both ends, under BORDER_MAX_KM apart,
            within BORDER_MAX_H), the pairs, the busiest day, the border posts
  zones     the UTC offsets seen, the westmost/eastmost, the biggest jump between
            two check-ins, the farthest from the home clock
  streaks   days abroad in a row, days in a row with a never-seen place / city,
            years in a row in one country, years in a row with a new country
  luck      the weather on the visit days against the 2011–2020 normal of those
            calendar months at the same point (ERA5 both): warmer/colder, rainier
  nearest   the first-level regions not visited yet, nearest to the current home
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime

from transform import group_category

from .trips import _home_at

BORDER_MAX_KM = 500       # farther in a day is a flight even without an airport row
BORDER_MAX_H = 24
ABROAD_GAP_DAYS = 3       # silent days inside a trip abroad don't break the run
LUCK_MIN_DAYS = 4
LIST_N = 10


def _km(la: float, ga: float, lb: float, gb: float) -> float:
    p1, p2 = math.radians(la), math.radians(lb)
    dp, dl = p2 - p1, math.radians(gb - ga)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


HOME_CATEGORY = "Home (private)"
HIDE_HOME = False      # config/privacy.json "hide_home" (set by compute_explore)


def _place(r: dict, d: datetime | None = None) -> dict:
    """A place as the page shows it; with HIDE_HOME a private home is "Home", not its address."""
    home = HIDE_HOME and group_category(r) == HOME_CATEGORY
    out = {"venue": "Home" if home else r.get("venue", ""), "venue_id": "" if home else r.get("venue_id", ""),
           "city": r.get("city", ""), "country": r.get("country", "")}
    if d is not None:
        out["date"] = d.strftime("%Y-%m-%d")
    return out


def _air(r: dict) -> bool:
    c = group_category(r)
    return "Airport" in c or c in {"Plane", "Airline", "Airport Gate"}


def _home_country_of(rows_or_pts) -> dict[str, str]:
    """city → its most common country (a gateway tagged with the neighbour loses)."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for p in rows_or_pts:
        r = p[0] if isinstance(p, tuple) else p
        if r.get("city") and r.get("country"):
            seen[r["city"]][r["country"]] += 1
    return {c: n.most_common(1)[0][0] for c, n in seen.items()}


# ── borders ─────────────────────────────────────────────────────────────────

def borders(pts: list[tuple[dict, datetime, float, float]]) -> dict:
    pairs: Counter = Counter()
    by_day: dict[str, list[tuple[str, str]]] = defaultdict(list)
    n = 0
    first: dict | None = None
    for (a, _da, la, ga), (b, db, lb, gb) in zip(pts, pts[1:]):
        ca, cb = a.get("country") or "", b.get("country") or ""
        if not ca or not cb or ca == cb:
            continue
        if int(b["date"]) - int(a["date"]) > BORDER_MAX_H * 3600:
            continue
        if (_air(a) and _air(b)) or _km(la, ga, lb, gb) > BORDER_MAX_KM:
            continue
        n += 1
        pairs[tuple(sorted((ca, cb)))] += 1
        by_day[db.strftime("%Y-%m-%d")].append((ca, cb))
        if first is None:
            first = {"from": ca, "to": cb, **_place(b, db)}
    posts: dict[str, list] = {}
    for r, d, _la, _ga in pts:
        if group_category(r) != "Border Crossing":
            continue
        vid = r.get("venue_id") or r.get("venue", "")
        if vid not in posts:
            posts[vid] = [_place(r, d), 0, d.strftime("%Y-%m-%d")]
        posts[vid][1] += 1
        posts[vid][2] = d.strftime("%Y-%m-%d")
    if not n and not posts:
        return {}
    # The busiest day by countries touched, then by crossings: six hops in and out of
    # the Vatican are not a bigger day than Belarus → Lithuania → Latvia.
    busy = (max(by_day.items(), key=lambda kv: (len({c for p in kv[1] for c in p}), len(kv[1]), kv[0]))
            if by_day else None)
    route: list[str] = []
    if busy:
        for ca, cb in busy[1]:
            if not route or route[-1] != ca:
                route.append(ca)
            route.append(cb)
    top_posts = sorted(posts.values(), key=lambda p: (-p[1], p[0]["venue"]))
    return {"crossings": n,
            "countries": len({c for pair in pairs for c in pair}),
            "pairs": [[a, b, k] for (a, b), k in pairs.most_common(15)],
            "pair_count": len(pairs),
            "busiest": {"date": busy[0], "n": len(busy[1]), "route": route} if busy else None,
            "first": first,
            "posts": [{**p[0], "visits": p[1], "last": p[2]} for p in top_posts[:15]],
            "post_count": len(posts)}


# ── time zones ──────────────────────────────────────────────────────────────

def _off(d: datetime) -> float | None:
    o = d.utcoffset()
    return None if o is None else round(o.total_seconds() / 3600, 2)


def zones(pts: list[tuple[dict, datetime, float, float]], home_city: str, home_history) -> dict:
    seen: dict[float, dict] = {}
    jump: tuple[float, dict] | None = None
    far: tuple[float, dict] | None = None
    home_off: float | None = None
    prev: tuple[dict, datetime, float] | None = None
    for r, d, _la, _ga in pts:
        off = _off(d)
        if off is None:
            continue
        if off not in seen:
            seen[off] = _place(r, d)
        if (r.get("city") or "") == _home_at(int(r["date"]), home_city, home_history):
            home_off = off
        elif home_off is not None and (far is None or abs(off - home_off) > abs(far[0])):
            far = (off - home_off, {**_place(r, d), "offset": off, "home": home_off})
        if prev is not None and int(r["date"]) - int(prev[0]["date"]) <= 48 * 3600:
            dj = off - prev[2]
            if jump is None or abs(dj) > abs(jump[0]):
                jump = (dj, {"from": _place(prev[0], prev[1]), "to": _place(r, d),
                             "from_off": prev[2], "to_off": off})
        prev = (r, d, off)
    if not seen:
        return {}
    w, e = min(seen), max(seen)
    return {"count": len(seen), "span": round(e - w, 2),
            "offsets": [[o, seen[o]] for o in sorted(seen)],
            "west": {**seen[w], "offset": w}, "east": {**seen[e], "offset": e},
            "jump": {"hours": jump[0], **jump[1]} if jump else None,
            "far": {"hours": far[0], **far[1]} if far else None}


# ── streaks ─────────────────────────────────────────────────────────────────

def _run(days: list[date]) -> tuple[int, date | None, date | None]:
    best, start, end = 0, None, None
    cur_s: date | None = None
    for i, d in enumerate(days):
        if i == 0 or (d - days[i - 1]).days != 1:
            cur_s = d
        assert cur_s is not None
        if (d - cur_s).days + 1 > best:
            best, start, end = (d - cur_s).days + 1, cur_s, d
    return best, start, end


def _year_run(years: list[int]) -> tuple[int, int, int]:
    best, s, e, cur = 0, 0, 0, 0
    for i, y in enumerate(years):
        cur = cur + 1 if i and y == years[i - 1] + 1 else 1
        if cur > best:
            best, s, e = cur, y - cur + 1, y
    return best, s, e


def streaks(pts: list[tuple[dict, datetime, float, float]], home_city: str, home_history) -> dict:
    if not pts:
        return {}
    home_ctry = _home_country_of(pts)
    day_ctry: dict[date, set[str]] = defaultdict(set)
    day_home: dict[date, str] = {}
    first_venue: dict[str, date] = {}
    first_city: dict[str, date] = {}
    ctry_years: dict[str, set[int]] = defaultdict(set)
    first_ctry: dict[str, int] = {}
    for r, d, _la, _ga in pts:
        day = d.date()
        c = r.get("country") or ""
        if not c:
            continue
        day_ctry[day].add(c)
        hc = _home_at(int(r["date"]), home_city, home_history)
        day_home[day] = home_ctry.get(hc, "")
        first_venue.setdefault(r.get("venue_id") or r.get("venue", ""), day)
        first_city.setdefault(f"{r.get('city', '')}|{c}", day)
        ctry_years[c].add(d.year)
        first_ctry.setdefault(c, d.year)

    # Days abroad in a row: abroad days with gaps of silent days up to ABROAD_GAP_DAYS,
    # broken by any day at home-country soil.
    runs: list[dict] = []
    cur: list[date] = []
    for day in sorted(day_ctry):
        abroad = day_home.get(day) and day_home[day] not in day_ctry[day]
        if abroad and cur and (day - cur[-1]).days <= ABROAD_GAP_DAYS:
            cur.append(day)
            continue
        if cur:
            runs.append({"days": (cur[-1] - cur[0]).days + 1, "from": cur[0].isoformat(), "to": cur[-1].isoformat(),
                         "countries": sorted({c for x in cur for c in day_ctry[x]})})
        cur = [day] if abroad else []
    if cur:
        runs.append({"days": (cur[-1] - cur[0]).days + 1, "from": cur[0].isoformat(), "to": cur[-1].isoformat(),
                     "countries": sorted({c for x in cur for c in day_ctry[x]})})
    runs.sort(key=lambda x: (-x["days"], x["from"]))

    nv, nv_s, nv_e = _run(sorted(set(first_venue.values())))
    nc, nc_s, nc_e = _run(sorted(set(first_city.values())))
    homes = set(home_ctry.get(c, "") for c in {home_city, *(h for _, h in (home_history or []))})
    loyal: list[dict] = []
    for c, ys in ctry_years.items():
        if c in homes:
            continue
        k, s, e = _year_run(sorted(ys))
        if k >= 2:
            loyal.append({"country": c, "years": k, "from": s, "to": e})
    loyal.sort(key=lambda x: (-x["years"], -x["to"], x["country"]))
    ny, ny_s, ny_e = _year_run(sorted(set(first_ctry.values())))
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"abroad": runs[:LIST_N],
            "new_places": {"days": nv, "from": iso(nv_s), "to": iso(nv_e)},
            "new_cities": {"days": nc, "from": iso(nc_s), "to": iso(nc_e)},
            "country_years": loyal[:LIST_N],
            "new_country_years": {"years": ny, "from": ny_s, "to": ny_e}}


# ── weather luck ────────────────────────────────────────────────────────────

def luck(wx: dict, normals: dict, home_cities: set[str]) -> dict:
    """Per city with LUCK_MIN_DAYS visit days whose months all have a normal:
    dt = mean(visit tmax − normal tmax of that month), rain seen vs expected
    (share of days with ≥ 1 mm)."""
    cities: list[dict] = []
    tot = {"all": [0.0, 0, 0.0, 0.0], "away": [0.0, 0, 0.0, 0.0]}
    for k, days in wx.items():
        nm = normals.get(k) or {}
        use = [(d, w) for d, w in days.items() if w and w[0] is not None and w[2] is not None and d[5:7] in nm]
        if len(use) < LUCK_MIN_DAYS:
            continue
        dt = sum(w[0] - nm[d[5:7]][0] for d, w in use) / len(use)
        seen = sum(1 for _d, w in use if w[2] >= 1) / len(use)
        exp = sum(nm[d[5:7]][2] for d, _w in use) / len(use)
        city, _, country = k.partition("|")
        cities.append({"city": city, "country": country, "days": len(use), "dt": round(dt, 1),
                       "rain": round(100 * seen), "rain_norm": round(100 * exp)})
        for part in ("all",) + (() if city in home_cities else ("away",)):
            t = tot[part]
            t[0] += dt * len(use)
            t[1] += len(use)
            t[2] += seen * len(use)
            t[3] += exp * len(use)
    if not cities:
        return {}
    big = [c for c in cities if c["days"] >= LUCK_MIN_DAYS]
    summary = {p: {"days": t[1], "dt": round(t[0] / t[1], 1), "rain": round(100 * t[2] / t[1]),
                   "rain_norm": round(100 * t[3] / t[1])} for p, t in tot.items() if t[1]}
    return {"summary": summary, "cities": len(big),
            "warmer": sorted(big, key=lambda c: (-c["dt"], -c["days"]))[:LIST_N],
            "colder": sorted(big, key=lambda c: (c["dt"], -c["days"]))[:LIST_N],
            "wetter": sorted(big, key=lambda c: (-(c["rain"] - c["rain_norm"]), -c["days"]))[:LIST_N],
            "drier": sorted(big, key=lambda c: (c["rain"] - c["rain_norm"], -c["days"]))[:LIST_N]}


# ── regions still missing ───────────────────────────────────────────────────

def nearest(regions_out: dict, iso: dict, flags: dict, home: tuple[float, float] | None) -> dict:
    """Unvisited first-level regions nearest to `home`, and for every country at
    least half covered the regions that would complete it."""
    if not regions_out:
        return {}
    visited = {g[2] for c in regions_out.get("countries", []) for g in c["regions"] if len(g) > 2}
    names, centre = iso.get("names") or {}, iso.get("centre") or {}
    ctry = {}
    for name, cc in flags.items():
        if not name.startswith("_"):
            ctry.setdefault(cc.upper(), name)
    out: dict = {}
    if home:
        cand = [(round(_km(home[0], home[1], la, lo)), code) for code, (la, lo) in centre.items()
                if code not in visited and code.split("-")[0] in ctry]
        cand.sort()
        out["home"] = [{"code": c, "name": names.get(c, c), "country": ctry.get(c.split("-")[0], ""), "km": km}
                       for km, c in cand[:12]]
    totals = iso.get("totals") or {}
    gaps = []
    for c in regions_out.get("countries", []):
        cc = (flags.get(c["country"]) or "").upper()
        if not c.get("of") or c["n"] >= c["of"] or c["n"] * 2 < c["of"] or not cc:
            continue
        miss = sorted(code for code in names if code.startswith(cc + "-") and code not in visited
                      and code in centre and code.split("-")[0] == cc and code not in (iso.get("parents") or {}))
        if home:
            miss.sort(key=lambda code: _km(home[0], home[1], *centre[code]))
        gaps.append({"country": c["country"], "n": c["n"], "of": totals.get(cc, c["of"]),
                     "missing": [names.get(m, m) for m in miss[:12]], "left": c["of"] - c["n"]})
    gaps.sort(key=lambda g: (g["left"], g["country"]))
    out["complete_next"] = gaps
    return out
