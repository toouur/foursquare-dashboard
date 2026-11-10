# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""rhythms.py — what the check-ins say about habits around travel. Same `pts` as
metrics.explore: (row, LOCAL datetime, lat, lng), real place check-ins, time order.

  soundtrack     Last.fm scrobbles placed at the last check-in before them (within
                 SOUND_MAX_H): top artists per country and city, and the artists that
                 belong to one country more than to the rest of the library
  weather_mood   which category groups gain or lose share on rainy / freezing / hot days
  abandoned      places once regular (ABANDON_MIN visits) not seen for ABANDON_YEARS
  city_returns   per city, the share of its places visited more than once
  jetlag         after a jump of JETLAG_MIN_H hours: first/last check-in hour on the
                 next days, against the home-day baseline
  holidays       New Year's Eves, and national public holidays (Nager.Date cache)
                 spent where they are a holiday
  companion_reach  countries / regions / cities seen with each companion
"""
from __future__ import annotations

import bisect
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from transform import group_category

from .companions import collect_companions
from .trips import _home_at

SOUND_MAX_H = 12
SOUND_MIN_COUNTRY = 50          # scrobbles for a country row
SOUND_MIN_CITY = 150
ABANDON_MIN = 10
ABANDON_YEARS = 2
RETURN_MIN_VENUES = 40
JETLAG_MIN_H = 3
JETLAG_DAYS = 5
MOOD_MIN = 30                   # check-ins of a group inside a weather bucket


DAY_START_H = 4                 # a check-in at 01:30 still belongs to the evening before


def _hour(d: datetime) -> float:
    return d.hour + d.minute / 60


def _wake_day(d: datetime) -> tuple[date, float]:
    """(day, hour) with the day starting at DAY_START_H: 01:30 → the day before, hour 25.5."""
    s = d - timedelta(hours=DAY_START_H)
    return s.date(), _hour(s) + DAY_START_H


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


def _home_country(pts) -> dict[str, str]:
    seen: dict[str, Counter] = defaultdict(Counter)
    for r, *_ in pts:
        if r.get("city") and r.get("country"):
            seen[r["city"]][r["country"]] += 1
    return {c: n.most_common(1)[0][0] for c, n in seen.items()}


# ── soundtrack of places ────────────────────────────────────────────────────

def soundtrack(pts: list, scrobbles: list[tuple[int, str, str, str]] | None) -> dict:
    if not pts or not scrobbles:
        return {}
    stamps = [int(r["date"]) for r, *_ in pts]
    by_c: dict[str, Counter] = defaultdict(Counter)
    tr_c: dict[str, Counter] = defaultdict(Counter)
    by_city: dict[str, Counter] = defaultdict(Counter)
    glob: Counter = Counter()
    placed = 0
    for ts, artist, track, _album in scrobbles:
        if not artist:
            continue
        i = bisect.bisect_right(stamps, ts) - 1
        if i < 0 or ts - stamps[i] > SOUND_MAX_H * 3600:
            continue
        r = pts[i][0]
        c = r.get("country") or ""
        if not c:
            continue
        placed += 1
        by_c[c][artist] += 1
        tr_c[c][(artist, track)] += 1
        by_city[f"{r.get('city', '')}|{c}"][artist] += 1
        glob[artist] += 1
    if not placed:
        return {}
    countries: list[dict] = []
    for c, arts in by_c.items():
        n = sum(arts.values())
        if n < SOUND_MIN_COUNTRY:
            continue
        (ta, tt), tn = tr_c[c].most_common(1)[0]
        countries.append({"country": c, "n": n, "artists": [[a, k] for a, k in arts.most_common(3)],
                          "track": [ta, tt, tn]})
    countries.sort(key=lambda x: -x["n"])
    cities: list[dict] = []
    for k, arts in by_city.items():
        n = sum(arts.values())
        if n >= SOUND_MIN_CITY:
            city, _, c = k.partition("|")
            a, an = arts.most_common(1)[0]
            cities.append({"city": city, "country": c, "n": n, "artist": a, "plays": an})
    cities.sort(key=lambda x: -x["n"])
    total = sum(glob.values())
    sig: list[dict] = []
    for c, arts in by_c.items():
        cn = sum(arts.values())
        if cn < SOUND_MIN_COUNTRY:
            continue
        for a, k in arts.items():
            if k < 15 or glob[a] < 20:
                continue
            lift = (k / cn) / (glob[a] / total)
            if lift >= 3 and k / glob[a] >= 0.5:
                sig.append({"artist": a, "country": c, "plays": k, "share": round(100 * k / glob[a]),
                            "lift": round(lift, 1)})
    sig.sort(key=lambda x: (-x["plays"] * x["lift"]))
    return {"placed": placed, "total": len(scrobbles), "countries": countries[:40],
            "cities": cities[:30], "signature": sig[:12]}


# ── weather and behaviour ───────────────────────────────────────────────────

WX_BUCKETS = {"rain": lambda w: w[2] >= 1,
              "freezing": lambda w: w[0] < 0, "hot": lambda w: w[0] >= 28}


def weather_mood(pts: list, wx: dict, categorize, skip: set[str]) -> dict:
    tot: Counter = Counter()
    bucket: dict[str, Counter] = {b: Counter() for b in WX_BUCKETS}
    for r, d, *_ in pts:
        w = (wx.get(f"{r.get('city', '')}|{r.get('country', '')}") or {}).get(d.strftime("%Y-%m-%d"))
        if not w or w[0] is None or w[2] is None:
            continue
        cat = group_category(r)
        if cat in skip:
            continue
        g = categorize(cat) if categorize else cat
        if not g or g == "Other":
            continue
        tot[g] += 1
        for b, f in WX_BUCKETS.items():
            if f(w):
                bucket[b][g] += 1
    if not tot:
        return {}
    n_all = sum(tot.values())
    out: dict = {"days_checkins": n_all, "buckets": {}}
    for b, cnt in bucket.items():
        nb = sum(cnt.values())
        if nb < 200:
            continue
        lifts = [(g, round((k / nb) / (tot[g] / n_all), 2), k) for g, k in cnt.items() if k >= MOOD_MIN]
        lifts.sort(key=lambda t: -t[1])
        out["buckets"][b] = {"n": nb, "more": [[g, lf, k] for g, lf, k in lifts[:5] if lf > 1.05],
                             "less": [[g, lf, k] for g, lf, k in lifts[::-1][:5] if lf < 0.95]}
    return out if out["buckets"] else {}


# ── places left behind, cities you return to ────────────────────────────────

def abandoned(pts: list, skip: set[str]) -> list[dict]:
    if not pts:
        return []
    end = max(int(r["date"]) for r, *_ in pts)
    v: dict[str, list] = {}
    for r, d, *_ in pts:
        vid = r.get("venue_id")
        if not vid or group_category(r) in skip:
            continue
        if vid not in v:
            v[vid] = [r, d, d, 0]
        v[vid][2] = d
        v[vid][3] += 1
    out: list[dict] = []
    for r, first, last, n in v.values():
        gone = (end - int(last.timestamp())) / (365.25 * 86400)
        if n >= ABANDON_MIN and gone >= ABANDON_YEARS:
            out.append({**_place(r), "visits": n, "first": first.strftime("%Y-%m-%d"),
                        "last": last.strftime("%Y-%m-%d"), "years_gone": round(gone, 1)})
    out.sort(key=lambda x: (-x["visits"], x["last"]))
    return out[:25]


def city_returns(pts: list, skip: set[str]) -> dict:
    per: dict[str, Counter] = defaultdict(Counter)
    for r, *_ in pts:
        if r.get("venue_id") and group_category(r) not in skip:
            per[f"{r.get('city', '')}|{r.get('country', '')}"][r["venue_id"]] += 1
    rows: list[dict] = []
    for k, vs in per.items():
        if len(vs) < RETURN_MIN_VENUES:
            continue
        city, _, c = k.partition("|")
        back = sum(1 for n in vs.values() if n > 1)
        rows.append({"city": city, "country": c, "venues": len(vs), "returned": back,
                     "pct": round(100 * back / len(vs))})
    if not rows:
        return {}
    rows.sort(key=lambda x: (-x["pct"], -x["venues"]))
    return {"loyal": rows[:10], "explorer": sorted(rows, key=lambda x: (x["pct"], -x["venues"]))[:10],
            "cities": len(rows)}


# ── jet lag ─────────────────────────────────────────────────────────────────

def jetlag(pts: list, home_city: str, home_history) -> dict:
    offs = []
    for r, d, *_ in pts:
        o = d.utcoffset()
        if o is not None:
            offs.append((r, d, o.total_seconds() / 3600))
    if len(offs) < 2:
        return {}
    days: dict[date, list[float]] = defaultdict(list)
    home_days: dict[date, list[float]] = defaultdict(list)
    for r, d, _o in offs:
        day, h = _wake_day(d)
        days[day].append(h)
        if (r.get("city") or "") == _home_at(int(r["date"]), home_city, home_history):
            home_days[day].append(h)
    if len(home_days) < 30:
        return {}
    base_first = statistics.median(min(h) for h in home_days.values())
    base_last = statistics.median(max(h) for h in home_days.values())
    agg: dict[str, dict[int, list[tuple[float, float]]]] = {"east": defaultdict(list), "west": defaultdict(list)}
    trips = []
    for i in range(1, len(offs)):
        r0, d0, o0 = offs[i - 1]
        r1, d1, o1 = offs[i]
        shift = o1 - o0
        if abs(shift) < JETLAG_MIN_H or int(r1["date"]) - int(r0["date"]) > 48 * 3600:
            continue
        side = "east" if shift > 0 else "west"
        arrive = _wake_day(d1)[0]
        firsts = []
        for k in range(1, JETLAG_DAYS + 1):
            day = arrive + timedelta(days=k)
            hs = [_wake_day(d)[1] for r, d, o in offs[i:i + 400] if _wake_day(d)[0] == day and o == o1]
            if not hs:
                continue
            agg[side][k].append((min(hs), max(hs)))
            firsts.append([k, round(min(hs), 1)])
        trips.append({"date": arrive.isoformat(), "from": r0.get("city", ""), "to": r1.get("city", ""),
                      "country": r1.get("country", ""), "shift": shift, "first": firsts})
    if not trips:
        return {}
    curve = {s: [[k, round(statistics.median(v[0] for v in a[k]), 1),
                  round(statistics.median(v[1] for v in a[k]), 1), len(a[k])]
                 for k in sorted(a) if len(a[k]) >= 2] for s, a in agg.items()}
    trips.sort(key=lambda t: (-abs(t["shift"]), t["date"]))
    return {"baseline": {"first": round(base_first, 1), "last": round(base_last, 1)},
            "curve": curve, "jumps": len(trips), "trips": trips[:12]}


# ── holidays ────────────────────────────────────────────────────────────────

def holidays(pts: list, hol: dict | None, flags: dict, home_city: str, home_history) -> dict:
    home_ctry = _home_country(pts)
    nye: dict[int, tuple] = {}
    for r, d, *_ in pts:
        if d.month == 12 and d.day == 31 and d.hour >= 18:
            nye[d.year] = (r, d)                         # the last one that evening
        elif d.month == 1 and d.day == 1 and d.hour < 6 and (d.year - 1) not in nye:
            nye[d.year - 1] = (r, d)
    eves = []
    for y in sorted(nye):
        r, d = nye[y]
        home = home_ctry.get(_home_at(int(r["date"]), home_city, home_history), "")
        eves.append({"year": y, **_place(r, d), "abroad": bool(home) and r.get("country") != home})
    out: dict = {"new_year": eves[::-1]} if eves else {}
    if hol:
        seen: dict[tuple[str, str], dict] = {}
        for r, d, *_ in pts:
            c = r.get("country") or ""
            cc = (flags.get(c) or "").upper()
            day = d.strftime("%Y-%m-%d")
            if not cc or (day, c) in seen:
                continue
            name = next((h[1] for h in hol.get(f"{cc}-{d.year}") or [] if h[0] == day), None)
            if name:
                home = home_ctry.get(_home_at(int(r["date"]), home_city, home_history), "")
                seen[(day, c)] = {"date": day, "holiday": name, **_place(r, d),
                                  "abroad": bool(home) and c != home}
        lst = sorted(seen.values(), key=lambda x: x["date"], reverse=True)
        if lst:
            names = Counter(x["holiday"] for x in lst if x["abroad"])
            out.update({"public": lst[:80], "count": len(lst), "abroad": sum(1 for x in lst if x["abroad"]),
                        "top_abroad": names.most_common(8)})
    return out


# ── companions across the map ───────────────────────────────────────────────

def companion_reach(pts: list, code_of) -> list[dict]:
    """[{name, checkins, countries, regions, cities, codes, country_list}] — `code_of`
    maps a row to its first-level region code (or None)."""
    acc: dict[str, dict] = {}
    for r, d, *_ in pts:
        names = collect_companions(r)
        if not names:
            continue
        code = code_of(r)
        for nm in names:
            a = acc.setdefault(nm, {"n": 0, "c": Counter(), "g": set(), "city": set(), "first": d})
            a["n"] += 1
            a["c"][r.get("country") or ""] += 1
            if code:
                a["g"].add(code)
            a["city"].add(f"{r.get('city', '')}|{r.get('country', '')}")
    out = [{"name": nm, "checkins": a["n"], "countries": len([c for c in a["c"] if c]),
            "regions": len(a["g"]), "cities": len(a["city"]), "codes": sorted(a["g"]),
            "country_list": [c for c, _ in a["c"].most_common() if c], "first": a["first"].strftime("%Y-%m-%d")}
           for nm, a in acc.items() if a["n"] >= 3]
    out.sort(key=lambda x: (-x["countries"], -x["regions"], -x["checkins"]))
    return out[:25]
