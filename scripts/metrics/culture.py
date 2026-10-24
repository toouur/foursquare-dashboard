# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Cultural chronicle — films (IMDb), wines (Vivino), sound (Last.fm) joined to
check-ins (metrics package).

Everything is placed on the check-in timeline:

* a wine scan (UTC ts) borrows the place of the nearest check-in within
  ``WINE_WINDOW_H`` hours; a bar / restaurant / winery within ``WINE_PREFER_H``
  wins over a closer bus stop, but not over where you actually were hours later
  (a lunchtime café must not claim the evening's bottle);
* a film is classified by its RATING date (IMDb has no viewing date): ``cinema``
  when a movie-theatre check-in sits on that local day or up to
  ``CINEMA_LAG_DAYS`` before it AND the film is new (released no earlier than
  the year before the visit — an old film rated the day after a premiere was
  watched at home), ``road`` when the day falls inside a trip,
  ``backlog`` for catch-up days (see culture_sources.BULK_DAY), else ``home``;
* scrobbles are summed per trip from its first to its last check-in.

The result feeds culture.html (wine map, films, sound), the trip modal
("Sound · Taste · Frame") and the "Year in culture" section of year pages.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .milestones import _f, _Localiser
from .trips import _parse_ts

CINEMA_CATEGORIES = {"Movie Theater", "Indie Movie Theater", "Multiplex", "Drive-in Theater"}
# no "cafe": a "Corporate Cafeteria" check-in 20 min before a supermarket scan
# claimed the bottle bought next door
WINE_PLACES = ("wine", "bar", "restaurant", "winery", "bistro", "brasserie", "trattoria",
               "pub", "tavern", "lounge", "steakhouse")
WINE_WINDOW_H = 4
WINE_PREFER_H = 0.75
CINEMA_LAG_DAYS = 2
TOP = 5


def _trip_spans(trips: list[dict]) -> list[tuple[str, str, int, str]]:
    out = []
    for t in trips:
        a, b = t.get("start_date"), t.get("end_date")
        if a and b and t.get("id") is not None:
            out.append((a, b, int(t["id"]), t.get("name") or ""))
    return sorted(out)


def _trip_on(day: str, spans: list[tuple[str, str, int, str]]) -> tuple[int, str] | None:
    for a, b, tid, name in spans:
        if a <= day <= b:
            return tid, name
        if a > day:
            break
    return None


def compute_culture(rows: list[dict], trips: list[dict] | None = None,
                    films: list[dict] | None = None, wines: list[dict] | None = None,
                    scrobbles: list[tuple[int, str, str, str]] | None = None) -> dict:
    trips = trips or []
    films = films or []
    wines = wines or []
    scrobbles = scrobbles or []
    spans = _trip_spans(trips)
    loc = _Localiser()

    # check-in timeline: (ts, local date, row)
    tl: list[tuple[int, date, dict]] = []
    for r in rows:
        d = _parse_ts(r)
        if d is None:
            continue
        ld = loc(d, _f(r.get("lat")), _f(r.get("lng")), (r.get("country") or "").strip())
        tl.append((int(d.timestamp()), ld.date(), r))
    tl.sort(key=lambda t: t[0])
    T = [t[0] for t in tl]
    cinema_days: dict[date, dict] = {}
    for _, ld, r in tl:
        if (r.get("category") or "") in CINEMA_CATEGORIES:
            cinema_days.setdefault(ld, r)

    # ── wines ───────────────────────────────────────────────────────────────
    wine_out: list[dict[str, Any]] = []
    for w in wines:
        rec: dict[str, Any] = {k: w.get(k) for k in ("wine", "winery", "vintage", "region", "country", "type",
                                      "rating", "avg", "review", "url", "label", "ts")}
        if w.get("ts") is not None:
            ts = int(w["ts"])
            win = int(WINE_WINDOW_H * 3600)
            best = None
            # every check-in within the window, however dense the day was
            for j in range(bisect.bisect_left(T, ts - win), bisect.bisect_right(T, ts + win)):
                dt_h = abs(tl[j][0] - ts) / 3600
                cat = (tl[j][2].get("category") or "").lower()
                wine_place = any(p in cat for p in WINE_PLACES) and dt_h <= WINE_PREFER_H
                score = (0 if wine_place else 1, dt_h)
                if best is None or score < best[0]:
                    best = (score, j)
            if best is not None:
                _, ld, r = tl[best[1]]
                rec.update({"date": ld.isoformat(), "venue": r.get("venue") or "",
                            "venue_id": r.get("venue_id") or "", "city": r.get("city") or "",
                            "at_country": r.get("country") or "",
                            "lat": _f(r.get("lat")), "lng": _f(r.get("lng"))})
            else:
                rec["date"] = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            tr = _trip_on(rec["date"], spans)
            if tr:
                rec["trip"], rec["trip_name"] = tr
        wine_out.append(rec)

    # ── films ───────────────────────────────────────────────────────────────
    film_out: list[dict[str, Any]] = []
    for f in films:
        frec: dict[str, Any] = {k: f.get(k) for k in ("id", "title", "rating", "imdb_rating", "date", "type",
                                      "year", "genres", "runtime", "directors", "url")}
        day = date.fromisoformat(f["date"])
        cin = next((cinema_days[day - timedelta(days=k)] for k in range(CINEMA_LAG_DAYS + 1)
                    if day - timedelta(days=k) in cinema_days
                    and (f.get("year") or 0) >= (day - timedelta(days=k)).year - 1), None)
        tr = _trip_on(f["date"], spans)
        if f.get("bulk"):
            frec["where"] = "backlog"
        elif cin is not None:
            frec.update({"where": "cinema", "venue": cin.get("venue") or "",
                        "venue_id": cin.get("venue_id") or "", "city": cin.get("city") or ""})
        elif tr:
            frec["where"] = "road"
        else:
            frec["where"] = "home"
        if tr and not f.get("bulk"):
            frec["trip"], frec["trip_name"] = tr
        film_out.append(frec)

    # ── per trip: sound · taste · frame ────────────────────────────────────
    S = [s[0] for s in scrobbles]
    by_trip: dict[int, dict] = {}
    trip_start = {int(t["id"]): t for t in trips if t.get("id") is not None}
    for tid, t in trip_start.items():
        a, b = _trip_bounds(t)
        sound = None
        if a is not None and b is not None and scrobbles:
            lo, hi = bisect.bisect_left(S, a), bisect.bisect_right(S, b)
            arts = Counter(scrobbles[k][1] for k in range(lo, hi))
            trks = Counter((scrobbles[k][1], scrobbles[k][2]) for k in range(lo, hi))
            if hi > lo:
                sound = {"n": hi - lo, "artists": arts.most_common(TOP),
                         "tracks": [[a_, t_, n] for (a_, t_), n in trks.most_common(TOP)]}
        tw = [w for w in wine_out if w.get("trip") == tid]
        tf = [f for f in film_out if f.get("trip") == tid]
        if sound or tw or tf:
            by_trip[tid] = {"sound": sound,
                            "wines": [_wine_short(w) for w in tw],
                            "films": [_film_short(f) for f in tf]}

    # ── per local year ──────────────────────────────────────────────────────
    years: dict[str, dict] = defaultdict(lambda: {"films": 0, "film_avg": None, "where": {},
                                                  "top_films": [], "wines": 0, "wine_avg": None,
                                                  "top_wines": [], "wine_countries": []})
    fy: dict[str, list[dict]] = defaultdict(list)
    for f in film_out:
        if f["where"] != "backlog":
            fy[f["date"][:4]].append(f)
    for y, fl in fy.items():
        Y = years[y]
        Y["films"] = len(fl)
        Y["film_avg"] = round(sum(f["rating"] for f in fl) / len(fl), 1)
        Y["where"] = dict(Counter(f["where"] for f in fl))
        Y["top_films"] = [_film_short(f) for f in sorted(fl, key=lambda f: (-f["rating"], f["date"]))[:TOP]]
    wy: dict[str, list[dict]] = defaultdict(list)
    for w in wine_out:
        if w.get("date"):
            wy[w["date"][:4]].append(w)
    for y, wl in wy.items():
        Y = years[y]
        Y["wines"] = len(wl)
        Y["wine_avg"] = round(sum(w["rating"] for w in wl) / len(wl), 2)
        Y["top_wines"] = [_wine_short(w) for w in sorted(wl, key=lambda w: (-w["rating"], w.get("ts") or 0))[:TOP]]
        Y["wine_countries"] = Counter(w["country"] for w in wl if w["country"]).most_common(TOP)
    if scrobbles:
        sy: dict[str, Counter] = defaultdict(Counter)
        for ts, art, trk, _ in scrobbles:
            sy[str(datetime.fromtimestamp(ts, tz=timezone.utc).year)][(art, trk)] += 1
        for y, c in sy.items():
            years[y]["top_tracks"] = [[a_, t_, n] for (a_, t_), n in c.most_common(TOP)]

    return {"films": film_out, "wines": wine_out, "trips": {str(k): v for k, v in by_trip.items()},
            "years": {y: years[y] for y in sorted(years)}}


def _trip_bounds(t: dict) -> tuple[int | None, int | None]:
    """First → last check-in of the trip: music on the way counts, the evening
    at home before departure does not."""
    ts = [int(c["ts"]) for c in t.get("checkins") or [] if c.get("ts") is not None]
    if ts:
        return min(ts), max(ts)
    a = t.get("start_ts")
    return (int(a), None) if a is not None else (None, None)


def _wine_short(w: dict) -> dict:
    return {k: w.get(k) for k in ("wine", "winery", "vintage", "country", "type", "rating", "date", "venue", "url")}


def _film_short(f: dict) -> dict:
    return {k: f.get(k) for k in ("title", "year", "rating", "date", "where", "url")}
