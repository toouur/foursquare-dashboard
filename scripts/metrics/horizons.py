# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""horizons.py — places in a wider frame. Same `pts` as metrics.explore: (row, LOCAL
datetime, lat, lng), real place check-ins (no rf, no whole-country rows), time order.

  unesco        World Heritage sites you checked in at or right next to
                (config/unesco.json: every component point with a radius)
  cuisines      the food passport: national cuisines by the category of the place
                (config/cuisines.json), where you ate each and whether at the source
  graveyard     visited places Foursquare now marks closed (venue_closed.json in the
                data repo, written by fetch_checkins.py from the check-in venues)
  gravity       the centre of gravity of each year's check-ins and how far it moved
  history       compact (day, lat, lng) points for the history player (own JSON file)
  then_now      one place photographed years apart (photos.json)
  city_twins    for each city, the city most like it by the mix of its categories
  forecast      when the next trip is likely, and the nearest countries not seen yet
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .trips import _home_at

R_KM = 6371.0088
THEN_NOW_MIN_DAYS = 5 * 365
TWIN_MIN_CHECKINS = 60
TWIN_MIN_VENUES = 25
HISTORY_DIGITS = 2              # ~1 km cells for the history player


def _km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def _place(r: dict, d: datetime | None = None) -> dict:
    out = {"venue": r.get("venue", ""), "venue_id": r.get("venue_id", ""),
           "city": r.get("city", ""), "country": r.get("country", "")}
    if d is not None:
        out["date"] = d.strftime("%Y-%m-%d")
    return out


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# ── UNESCO World Heritage ─────────────────────────────────────────────────────

def unesco(pts, sites: dict, skip: set[str] | frozenset = frozenset()) -> dict:
    """Sites with a check-in inside one of their component circles.

    `sites`: id → [name, [countries], [[lat, lng, radius_m], …]]. A check-in counts
    for the site whose component centre is nearest among those it falls inside.
    """
    if not sites:
        return {}
    grid: dict[tuple[int, int], list[tuple[float, float, float, str]]] = defaultdict(list)
    for sid, (_name, _ctry, comps) in sites.items():
        for lat, lng, rad in comps:
            grid[(math.floor(lat), math.floor(lng))].append((lat, lng, rad / 1000.0, sid))

    hit: dict[str, dict] = {}
    per_venue: dict[str, str | None] = {}
    for r, d, lat, lng in pts:
        if (r.get("category") or "") in skip:
            continue
        vid = r.get("venue_id") or f"{lat:.4f},{lng:.4f}"
        if vid not in per_venue:
            best, best_km = None, 1e9
            ci, cj = math.floor(lat), math.floor(lng)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for slat, slng, srad, sid in grid.get((ci + di, cj + dj), ()):
                        if abs(slat - lat) > srad / 100:      # cheap reject (1° ≈ 111 km)
                            continue
                        k = _km(lat, lng, slat, slng)
                        if k <= srad and k < best_km:
                            best, best_km = sid, k
            per_venue[vid] = best
        sid = per_venue[vid]
        if not sid:
            continue
        h = hit.get(sid)
        if h is None:
            h = hit[sid] = {"id": sid, "name": _cap(sites[sid][0]), "first": d.strftime("%Y-%m-%d"),
                            "n": 0, "venues": Counter(), "names": {}, "years": set(),
                            "country": r.get("country", "")}
        h["n"] += 1
        h["years"].add(d.year)
        h["last"] = d.strftime("%Y-%m-%d")
        h["venues"][r.get("venue_id", "")] += 1
        h["names"][r.get("venue_id", "")] = r.get("venue", "")
    if not hit:
        return {}

    seen_by_c: Counter[str] = Counter(h["country"] for h in hit.values())
    out_sites = []
    for h in sorted(hit.values(), key=lambda h: h["first"]):
        vid, _n = h["venues"].most_common(1)[0]
        out_sites.append({"id": h["id"], "name": h["name"], "country": h["country"],
                          "first": h["first"], "last": h["last"], "n": h["n"],
                          "years": len(h["years"]), "venue": h["names"].get(vid, ""), "venue_id": vid})
    by_year = Counter(s["first"][:4] for s in out_sites)
    return {"n": len(out_sites), "total": len(sites), "sites": out_sites,
            "countries": [[c, n] for c, n in seen_by_c.most_common()],
            "by_year": sorted(by_year.items()),
            "most": max(out_sites, key=lambda s: (s["n"], s["first"]))}


# ── Food passport ─────────────────────────────────────────────────────────────

def cuisines(pts, cmap: dict, flags: dict) -> dict:
    """National cuisines eaten: per cuisine country, check-ins, places, the countries
    you ate it in, and whether you ate it at the source too."""
    if not cmap:
        return {}
    iso = {k: (v or "").lower() for k, v in flags.items()}
    by: dict[str, dict] = {}
    for r, d, _lat, _lng in pts:
        src = cmap.get((r.get("category") or "").strip())
        if not src:
            continue
        g = by.get(src)
        if g is None:
            g = by[src] = {"cuisine": src, "n": 0, "venues": Counter(), "names": {},
                           "where": Counter(), "first": d.strftime("%Y-%m-%d"), "source": 0}
        g["n"] += 1
        vid = r.get("venue_id", "")
        g["venues"][vid] += 1
        g["names"][vid] = (r.get("venue", ""), r.get("city", ""))
        here = r.get("country", "")
        g["where"][here] += 1
        if iso.get(here) and iso.get(here) == iso.get(src):
            g["source"] += 1
    if not by:
        return {}
    rows = []
    for g in by.values():
        vid, vn = g["venues"].most_common(1)[0]
        rows.append({"cuisine": g["cuisine"], "flag": iso.get(g["cuisine"], ""), "n": g["n"],
                     "places": len(g["venues"]), "first": g["first"], "at_source": g["source"],
                     "where": [[c, n] for c, n in g["where"].most_common()],
                     "venue": g["names"][vid][0], "venue_id": vid, "city": g["names"][vid][1], "visits": vn})
    rows.sort(key=lambda x: (-x["n"], x["cuisine"]))
    abroad = [x for x in rows if x["at_source"] == 0]
    return {"cuisines": rows, "n": len(rows), "places": sum(x["places"] for x in rows),
            "widest": max(rows, key=lambda x: (len(x["where"]), x["n"]))["cuisine"],
            "never_at_source": [x["cuisine"] for x in abroad]}


# ── Places that are gone ──────────────────────────────────────────────────────

def graveyard(rows: list[dict], closed: dict | set, skip: set[str] | frozenset = frozenset(),
              stamp=None, top: int = 20) -> dict:
    """Visited places Foursquare marks closed. Every row counts (rf visits too: an old
    place you remember is exactly the kind that closes)."""
    if not closed:
        return {}
    v: dict[str, dict] = {}
    for r in rows:
        vid = (r.get("venue_id") or "").strip()
        if not vid or (r.get("category") or "") in skip:
            continue
        d = stamp(r) if stamp else None
        if d is None:
            continue
        g = v.get(vid)
        if g is None:
            g = v[vid] = {"venue_id": vid, "n": 0, "first": d, "last": d}
        g["n"] += 1
        g["first"], g["last"] = min(g["first"], d), max(g["last"], d)
        g.update(venue=r.get("venue", ""), city=r.get("city", ""), country=r.get("country", ""),
                 category=r.get("category", ""))
    gone = [g for vid, g in v.items() if vid in closed]
    if not gone:
        return {}
    by_year_all: Counter[int] = Counter(g["first"].year for g in v.values())
    by_year_gone: Counter[int] = Counter(g["first"].year for g in gone)
    by_cat: Counter[str] = Counter(g["category"] for g in gone)
    cat_all: Counter[str] = Counter(g["category"] for g in v.values())

    def out(g):
        return {"venue": g["venue"], "venue_id": g["venue_id"], "city": g["city"], "country": g["country"],
                "category": g["category"], "n": g["n"], "first": g["first"].strftime("%Y-%m-%d"),
                "last": g["last"].strftime("%Y-%m-%d")}
    loved = sorted(gone, key=lambda g: (-g["n"], g["venue"]))[:top]
    return {"n": len(gone), "of": len(v), "pct": round(100 * len(gone) / len(v), 1),
            "visits": sum(g["n"] for g in gone),
            "loved": [out(g) for g in loved],
            "by_year": [[y, by_year_gone.get(y, 0), by_year_all[y]] for y in sorted(by_year_all)],
            "categories": [[c, n, round(100 * n / cat_all[c])] for c, n in by_cat.most_common()
                           if cat_all[c] >= 20][:8]}


# ── Centre of gravity ─────────────────────────────────────────────────────────

def _mean_point(ps: list[tuple[float, float]]) -> tuple[float, float]:
    x = y = z = 0.0
    for lat, lng in ps:
        p, lam = math.radians(lat), math.radians(lng)
        x += math.cos(p) * math.cos(lam)
        y += math.cos(p) * math.sin(lam)
        z += math.sin(p)
    n = len(ps)
    x, y, z = x / n, y / n, z / n
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def gravity(pts, home_city: str = "", home_history=None) -> dict:
    """Per year: the mean point of all check-ins and of the ones away from home, with
    the nearest city you have checked in at."""
    if not pts:
        return {}
    city_pts: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    years: dict[int, list] = defaultdict(list)
    away: dict[int, list] = defaultdict(list)
    for r, d, lat, lng in pts:
        c = (r.get("city") or "", r.get("country") or "")
        if c[0]:
            city_pts[c].append((lat, lng))
        years[d.year].append((lat, lng))
        home = _home_at(int(r["date"]), home_city, home_history) if home_city else ""
        if not home or r.get("city") != home:
            away[d.year].append((lat, lng))
    cities = []
    for (city, ctry), ps in city_pts.items():
        if len(ps) >= 3:
            la = sorted(p[0] for p in ps)[len(ps) // 2]
            ln = sorted(p[1] for p in ps)[len(ps) // 2]
            cities.append((city, ctry, la, ln))

    def near(lat, lng):
        best = min(cities, key=lambda c: _km(lat, lng, c[2], c[3])) if cities else None
        return [best[0], best[1], round(_km(lat, lng, best[2], best[3]))] if best else None

    out, prev = [], None
    for y in sorted(years):
        lat, lng = _mean_point(years[y])
        row = {"year": y, "lat": round(lat, 3), "lng": round(lng, 3), "n": len(years[y]),
               "near": near(lat, lng)}
        if len(away[y]) >= 20:
            alat, alng = _mean_point(away[y])
            row["away"] = [round(alat, 3), round(alng, 3)]
            row["away_near"] = near(alat, alng)
        row["moved"] = round(_km(prev[0], prev[1], lat, lng)) if prev else 0
        prev = (lat, lng)
        out.append(row)
    biggest = max(out[1:], key=lambda r: r["moved"]) if len(out) > 1 else None
    return {"years": out, "jump": {"year": biggest["year"], "km": biggest["moved"]} if biggest else None}


# ── History player ────────────────────────────────────────────────────────────

def history(pts) -> dict:
    """{d0, p: [[day offset, lat·100, lng·100], …]} — one point per (day, ~1 km cell)."""
    if not pts:
        return {}
    d0 = min(d.date() for _r, d, _a, _b in pts)
    seen: set[tuple[int, int, int]] = set()
    p = []
    m = 10 ** HISTORY_DIGITS
    for _r, d, lat, lng in sorted(pts, key=lambda t: t[1].replace(tzinfo=None)):
        k = ((d.date() - d0).days, round(lat * m), round(lng * m))
        if k not in seen:
            seen.add(k)
            p.append(list(k))
    return {"d0": d0.isoformat(), "scale": m, "p": p}


# ── Then and now ──────────────────────────────────────────────────────────────

def then_now(rows: list[dict], photos: dict, stamp, skip: set[str] | frozenset = frozenset(),
             top: int = 12) -> list[dict]:
    """Places with a photo from two visits THEN_NOW_MIN_DAYS or more apart: the
    earliest and the latest photo."""
    if not photos:
        return []
    by_v: dict[str, list] = defaultdict(list)
    meta: dict[str, dict] = {}
    for r in rows:
        cid, vid = r.get("checkin_id") or "", r.get("venue_id") or ""
        files = photos.get(cid) if cid else None
        if not files or not vid or (r.get("category") or "") in skip:
            continue
        d = stamp(r)
        if d is None:
            continue
        by_v[vid].append((d, files[0], cid))
        meta[vid] = _place(r)
    out = []
    for vid, ph in by_v.items():
        ph.sort(key=lambda t: t[0].replace(tzinfo=None))
        a, b = ph[0], ph[-1]
        gap = (b[0].date() - a[0].date()).days
        if gap >= THEN_NOW_MIN_DAYS:
            out.append({**meta[vid], "a": {"date": a[0].strftime("%Y-%m-%d"), "src": a[1]},
                        "b": {"date": b[0].strftime("%Y-%m-%d"), "src": b[1]},
                        "years": round(gap / 365.25, 1), "photos": len(ph)})
    out.sort(key=lambda x: (-x["years"], x["venue"]))
    return out[:top]


# ── City twins ────────────────────────────────────────────────────────────────

def city_twins(pts, skip: set[str] | frozenset = frozenset(), top: int = 15, home: str = "") -> dict:
    """Cosine similarity of the category mix (√ of counts) between cities with
    TWIN_MIN_CHECKINS+ check-ins at TWIN_MIN_VENUES+ places."""
    cats: dict[tuple[str, str], Counter] = defaultdict(Counter)
    venues: dict[tuple[str, str], set] = defaultdict(set)
    for r, _d, _a, _b in pts:
        c = (r.get("city") or "", r.get("country") or "")
        cat = r.get("category") or ""
        if not c[0] or not cat or cat in skip:
            continue
        cats[c][cat] += 1
        venues[c].add(r.get("venue_id"))
    keys = [c for c in cats if sum(cats[c].values()) >= TWIN_MIN_CHECKINS and len(venues[c]) >= TWIN_MIN_VENUES]
    if len(keys) < 3:
        return {}
    # √count × idf: a category every city has (Park, Monument) says little.
    df: Counter[str] = Counter(k for c in keys for k in cats[c])
    idf = {k: math.log(len(keys) / n) + 0.1 for k, n in df.items()}
    vec = {}
    for c in keys:
        v = {k: math.sqrt(n) * idf[k] for k, n in cats[c].items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        vec[c] = {k: x / norm for k, x in v.items()}

    def sim(a, b):
        va, vb = vec[a], vec[b]
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(k, 0) for k, x in va.items())

    best: dict[tuple[str, str], tuple[float, tuple[str, str]]] = {}
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            s = sim(a, b)
            pairs.append((s, a, b))
            if s > best.get(a, (-1, a))[0]:
                best[a] = (s, b)
            if s > best.get(b, (-1, b))[0]:
                best[b] = (s, a)

    def shared(a, b, k=3):
        common = [(min(vec[a][x], vec[b].get(x, 0)), x) for x in vec[a] if x in vec[b]]
        return [x for _s, x in sorted(common, reverse=True)[:k]]
    pairs.sort(reverse=True)
    cross = [p for p in pairs if p[1][1] != p[2][1]]
    cities = {f"{a[0]}|{a[1]}": [b[0], b[1], round(100 * s)] for a, (s, b) in best.items()}
    return {"pairs": [{"a": a[0], "a_country": a[1], "b": b[0], "b_country": b[1], "sim": round(100 * s),
                       "shared": shared(a, b)} for s, a, b in cross[:top]],
            "loners": [{"city": c[0], "country": c[1], "twin": best[c][1][0], "sim": round(100 * best[c][0])}
                       for c in sorted(best, key=lambda c: best[c][0])[:6]],
            "cities": cities, "n": len(keys),
            "home": next(([c[0], c[1], *cities[f"{c[0]}|{c[1]}"]] for c in best if c[0] == home), None)}


# ── Next trip forecast ────────────────────────────────────────────────────────

def forecast(trips: list[dict] | None, countries_geo: dict, flags: dict, visited: set[str],
             home: tuple[float, float] | None, today: date | None = None) -> dict:
    """When the next trip is likely (median gap between recent trip starts, and the
    month trips have started in most), and the nearest countries not visited yet."""
    today = today or date.today()
    out: dict = {}
    starts = sorted(date.fromisoformat(t["start_date"]) for t in trips or [] if t.get("start_date"))
    if len(starts) >= 6:
        recent = starts[-25:]
        gaps = [(b - a).days for a, b in zip(recent, recent[1:]) if (b - a).days > 0]
        med = statistics.median(gaps) if gaps else None
        months = Counter()
        for s in starts:
            months[s.month] += 2 if s.year >= today.year - 3 else 1
        last_end = max(date.fromisoformat(t["end_date"]) for t in trips or [] if t.get("end_date"))
        ahead = [((today.month - 1 + k) % 12) + 1 for k in range(0, 4)]
        likely = max(ahead, key=lambda m: (months[m], -ahead.index(m)))
        went: Counter[str] = Counter()
        for t in trips or []:
            if t.get("start_date") and int(t["start_date"][5:7]) == likely:
                for c in t.get("countries") or []:
                    went[c if isinstance(c, str) else c[0]] += 1
        due = starts[-1] + timedelta(days=round(med)) if med else None
        out.update({"trips": len(starts), "median_gap": round(med) if med else None,
                    "last_start": starts[-1].isoformat(), "last_end": last_end.isoformat(),
                    "since": (today - last_end).days, "due": due.isoformat() if due else None,
                    "overdue": (today - due).days if due and due < today else 0,
                    "month": likely, "months": [months[m] for m in range(1, 13)],
                    "month_countries": [[c, n] for c, n in went.most_common(4)]})
    if home and countries_geo:
        seen = {(flags.get(c) or "").upper() for c in visited}
        cand = [(round(_km(home[0], home[1], v[1], v[2])), iso, v[0]) for iso, v in countries_geo.items()
                if not iso.startswith("_") and iso not in seen]
        cand.sort()
        out["nearest_new"] = [{"country": n, "iso": iso.lower(), "km": km} for km, iso, n in cand[:6]]
    return out
