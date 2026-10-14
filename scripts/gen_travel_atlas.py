# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Travel atlas journal — one standalone HTML page, read like a magazine.

Chapters instead of an endless feed: cover, world atlas, a spread per country,
a spread per trip, a year-by-year chronicle, a paged photo archive, a place
explorer and next-trip ideas. Everything (data + JS + CSS) is inlined, so the
file opens straight from disk on a phone.

Opt-in from build.py (`--travel-atlas-out PATH`): it is handed the SAME `rows`,
`stats` and `trips` objects the dashboard renders from, so every number in the
journal is the dashboard's number, not a re-count:

  * headline KPIs, countries, cities, years, trips  ← metrics.process() stats/trips
  * photo total = photos.json files + tip photos    ← build.py total_photos
  * per-country spread                              ← gen_country_pages formulas
  * place explorer                                  ← stats unique_places keying
                                                      (venue_id, else coords@3dp)

Nothing is filtered out — home check-ins included, exactly like the dashboard.

    python scripts/build.py --input .../checkins.csv --config-dir config \\
      --output-dir _site --photos .../photos.json --travel-atlas-out _site/travel-atlas.html
"""
from __future__ import annotations

import bisect
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "travel_atlas.html.tmpl"
DEFAULT_PIX = "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix/"
PIX_PREFIX = "29447180_"           # common R2 key prefix, stripped to save ~200 KB
EPOCH = date(2000, 1, 1)           # compact dates: days since EPOCH
SIGHT = ("Monument", "Plaza", "Historic", "Palace", "Castle", "Church", "Cathedral", "Mosque",
         "Temple", "Bridge", "Beach", "Scenic", "Lookout", "Mountain", "Lake", "River", "Harbor",
         "Marina", "Museum", "Garden", "Park", "Waterfront", "Landmark", "Fountain", "Square",
         "Tower", "Street", "Neighborhood", "Island", "Canal", "Pier", "Lighthouse", "Fort", "Ruin",
         "Shrine", "Synagogue", "Basilica", "Hill", "Nature", "Waterfall", "Canyon", "Desert",
         "Sculpture", "Memorial", "Gate", "Village", "Town", "City", "Capitol", "Pedestrian")


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _dn(day: str) -> int:
    return (date.fromisoformat(day) - EPOCH).days


def _km(a: float, b: float, c: float, d: float) -> float:
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _ts(r: dict) -> int:
    try:
        return int(r.get("date") or 0)
    except (TypeError, ValueError):
        return 0


def _coords(r: dict) -> tuple[float, float] | None:
    try:
        return float(r["lat"]), float(r["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _place_key(r: dict) -> Any:
    """Same identity metrics.stats uses for unique_places_count."""
    vid = (r.get("venue_id") or "").strip()
    if vid:
        return vid
    c = _coords(r)
    return (round(c[0], 3), round(c[1], 3)) if c else None


def _sight(cat: str) -> bool:
    return any(s in cat for s in SIGHT)


def _pick(ph: list[dict], k: int) -> list[dict]:
    """Diverse highlight pick: best score first, one per place, then by time."""
    seen: set = set()
    out: list[dict] = []
    for p in sorted(ph, key=lambda x: (-x["score"], x["ts"])):
        if p["where"] in seen:
            continue
        seen.add(p["where"])
        out.append(p)
        if len(out) >= k:
            break
    return sorted(out, key=lambda x: x["ts"])


def build_payload(rows: list[dict], stats: dict[str, Any], trips: list[dict],
                  photos_by_checkin: dict[str, list[str]], tips: list[dict],
                  cfg: dict[str, Any], flags: dict[str, str],
                  ctry_norm: dict[str, str] | None = None,
                  pix_url: str = DEFAULT_PIX) -> dict[str, Any]:
    """rows: the transformed rows build.py passed to metrics.process();
    stats/trips: what process() returned (trips after naming)."""
    ctry_norm = ctry_norm or {}
    iso_num: dict[str, str] = cfg.get("iso_numeric", {})
    curated_ids = set(cfg.get("curated_checkins", []))
    audit: dict[str, dict] = cfg.get("landmark_audit", {})

    countries = [c for c, _ in stats["countries"]]
    cidx = {c: i for i, c in enumerate(countries)}
    rows = sorted(rows, key=_ts)
    catlist = [c for c, _ in Counter((r.get("category") or "").strip() for r in rows
                                     if (r.get("category") or "").strip()).most_common()]
    catidx = {c: i for i, c in enumerate(catlist)}

    # ---- places: one per stats identity (venue_id, else rounded coords) ----
    pl: dict[Any, dict] = {}
    for r in rows:
        k = _place_key(r)
        if k is None:
            continue
        ts, c = _ts(r), _coords(r)
        p = pl.get(k)
        if p is None:
            p = pl[k] = {"name": (r.get("venue") or "").strip(), "city": (r.get("city") or "").strip(),
                         "cat": (r.get("category") or "").strip(), "country": (r.get("country") or "").strip(),
                         "id": k if isinstance(k, str) else "", "n": 0, "first": ts, "last": ts,
                         "ll": c}
        p["n"] += 1
        p["last"] = max(p["last"], ts)
        for f, col in (("city", "city"), ("cat", "category"), ("country", "country")):
            if not p[f] and (r.get(col) or "").strip():
                p[f] = r[col].strip()
        if p["ll"] is None and c:
            p["ll"] = c
    keys = sorted(pl, key=lambda k: -pl[k]["n"])
    pidx = {k: i for i, k in enumerate(keys)}
    assert len(keys) == stats.get("unique_places_count", len(keys)), \
        f"place identity drifted from metrics.stats: {len(keys)} vs {stats.get('unique_places_count')}"

    # ---- photos: every photos.json file of a known check-in + every tip photo ----
    photos: list[dict] = []
    for r in rows:
        files = photos_by_checkin.get(r.get("checkin_id", ""))
        if not files:
            continue
        k = _place_key(r)
        country = (r.get("country") or "").strip()
        base = {"ts": _ts(r), "country": country, "cid": r.get("checkin_id", ""),
                "score": (3 if r.get("checkin_id") in curated_ids else 0) + _sight(r.get("category") or ""),
                "where": k if k is not None else ("n", r.get("venue"), r.get("city"))}
        if k is not None:
            base["p"] = pidx[k]
        else:   # no venue id and no coords: carry the label inline
            base["inline"] = [(r.get("venue") or "").strip(), (r.get("city") or "").strip(),
                              cidx.get(country, -1), catidx.get((r.get("category") or "").strip(), -1), ""]
        for f in files:
            photos.append({**base, "f": f})
    for t in tips:
        if not t.get("photo"):
            continue
        country = ctry_norm.get(t.get("country", ""), t.get("country", ""))
        vid = (t.get("venue_id") or "").strip()
        photos.append({"f": t["photo"], "ts": int(t.get("ts") or 0), "country": country, "cid": "",
                       "score": 0, "where": vid or t.get("id"), "tip": 1,
                       **({"p": pidx[vid]} if vid in pidx else
                          {"inline": [t.get("venue", ""), t.get("city", ""), cidx.get(country, -1),
                                      catidx.get(t.get("category", ""), -1), vid]})})
    photos.sort(key=lambda p: p["ts"])
    for i, p in enumerate(photos):
        p["i"] = i
    ph_by_country: dict[str, list[dict]] = defaultdict(list)
    for p in photos:
        ph_by_country[p["country"]].append(p)

    # ---- countries: gen_country_pages formulas ----
    crow: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        c = (r.get("country") or "").strip()
        if c:
            crow[c].append(r)
    cout = []
    for c in countries:
        rs = crow[c]
        venues: Counter = Counter()
        vnames: dict[str, str] = {}
        cities: Counter = Counter()
        cats: Counter = Counter()
        days = set()
        city_ll: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for r in rs:
            vid, vname = (r.get("venue_id") or "").strip(), (r.get("venue") or "").strip()
            if vname:
                venues[vid or vname] += 1
                vnames.setdefault(vid or vname, vname)
            cy = (r.get("city") or "").strip()
            if cy:
                cities[cy] += 1
                ll = _coords(r)
                if ll:
                    city_ll[cy].append(ll)
            cat = (r.get("category") or "").strip()
            if cat:
                cats[cat] += 1
            if _ts(r):
                days.add(_day(_ts(r)))
        tss = sorted(t for t in map(_ts, rs) if t)
        top_cities = []
        for cy, n in cities.most_common(10):
            pts = sorted(city_ll[cy])
            m = pts[len(pts) // 2] if pts else None
            top_cities.append([cy, n, round(m[0], 4) if m else None, round(m[1], 4) if m else None])
        cout.append({
            "en": c, "iso2": flags.get(c, ""), "num": iso_num.get(c, ""),
            "n": len(rs), "v": len(venues), "c": len(cities), "d": len(days),
            "first": _day(tss[0]) if tss else "", "last": _day(tss[-1]) if tss else "",
            "years": len({d[:4] for d in days}),
            "cities": top_cities,
            "venues": [[vnames[k], n] for k, n in venues.most_common(10)],
            "cats": [[k, n] for k, n in cats.most_common(5)],
            "ph": [p["i"] for p in _pick(ph_by_country[c], 9)], "phn": len(ph_by_country[c]),
            "audit": audit.get(c),
        })

    # ---- trips (as rendered on trips.html) ----
    ph_ts = [p["ts"] for p in photos]
    tout = []
    for t in sorted(trips, key=lambda t: t["start_ts"]):
        end = int(datetime.strptime(t["end_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 30 * 3600
        lo, hi = bisect.bisect_left(ph_ts, t["start_ts"]), bisect.bisect_right(ph_ts, end)
        tc = set(t["countries"])
        tph = [p for p in photos[lo:hi] if p["country"] in tc]
        tout.append({"id": t["id"], "name": t["name"], "s": t["start_date"], "e": t["end_date"],
                     "dur": t["duration"], "n": t["checkin_count"], "u": t["unique_places"],
                     "co": t["countries"], "ci": t["cities"][:14], "cin": len(t["cities"]),
                     "ph": [p["i"] for p in _pick(tph, 9)], "phn": len(tph)})

    # ---- years: stats year_summaries / by_year / new_country_by_year ----
    by_year = dict(stats["by_year"])
    new_by_year = {str(y): cos for y, cos in stats.get("new_country_by_year", [])}
    ys = {str(y["year"]): y for y in stats.get("year_summaries", [])}
    ph_year = Counter(_day(p["ts"])[:4] for p in photos if p["ts"])
    tr_year = Counter(t["start_date"][:4] for t in trips)
    years = [{"y": y, "n": n, "co": ys.get(y, {}).get("countries", 0), "ci": ys.get(y, {}).get("cities", 0),
              "new": new_by_year.get(y, []), "tr": tr_year[y], "ph": ph_year[y]}
             for y, n in sorted(by_year.items())]

    # ---- map: stats cities + centroids; country-to-country moves ----
    cent = stats.get("city_centroids", {})
    mapcities = [[cy, cidx[co], n, cent[cy][0], cent[cy][1]]
                 for cy, n, co in stats["cities"] if cy in cent and co in cidx][:160]
    routes: dict[tuple, list] = {}
    prev = None
    for r in rows:
        ll, co = _coords(r), (r.get("country") or "").strip()
        if not co or not ll or ll == (0.0, 0.0):
            continue
        if prev and prev[1] != co:
            day = _day(_ts(r))
            k = (prev[2], r.get("city", ""), day[:4])
            if k not in routes:
                routes[k] = [round(prev[0][0], 2), round(prev[0][1], 2), round(ll[0], 2), round(ll[1], 2),
                             int(day[:4]), cidx[prev[1]], cidx[co], prev[2], r.get("city", ""), day,
                             round(_km(prev[0][0], prev[0][1], ll[0], ll[1]))]
        prev = (ll, co, r.get("city", ""))

    def _photo_out(p: dict) -> list:
        f = p["f"][len(PIX_PREFIX):] if p["f"].startswith(PIX_PREFIX) else "!" + p["f"]
        out = [f, p.get("p", -1), _dn(_day(p["ts"]))]
        if "inline" in p:
            out.append(p["inline"])
        elif p.get("tip"):
            out.append(None)
        if p.get("tip"):
            out.append(1)
        return out

    return {
        "meta": {"checkins": stats["total"], "refurb": stats.get("refurb_checkins", 0),
                 "countries": len(stats["countries"]), "cities": len(stats["cities"]),
                 "venues": stats["unique_places_count"], "photos": len(photos),
                 "photoCheckins": len({p["cid"] for p in photos if p["cid"]}),
                 "tipPhotos": sum(1 for p in photos if p.get("tip")),
                 "trips": len(trips), "first": stats["date_min"], "last": stats["date_max"],
                 "days": len({_day(_ts(r)) for r in rows if _ts(r)}),
                 "built": date.today().isoformat(), "pix": pix_url},
        "C": countries, "K": catlist, "countries": cout, "trips": tout, "years": years,
        "cities": mapcities, "routes": list(routes.values()),
        # place: [name, city, countryIdx, lat, lng, n, first (0 = same as last), last, catIdx, venueId]
        "places": [[pl[k]["name"], pl[k]["city"], cidx.get(pl[k]["country"], -1),
                    round(pl[k]["ll"][0], 4) if pl[k]["ll"] else None,
                    round(pl[k]["ll"][1], 4) if pl[k]["ll"] else None,
                    pl[k]["n"],
                    0 if _day(pl[k]["first"]) == _day(pl[k]["last"]) else _dn(_day(pl[k]["first"])),
                    _dn(_day(pl[k]["last"])), catidx.get(pl[k]["cat"], -1), pl[k]["id"]] for k in keys],
        # photo: [file ('!' = full key, else PIX_PREFIX stripped), placeIdx (-1 = inline),
        #         day, (inline [name, city, countryIdx, catIdx, venueId] | null), (1 = tip photo)]
        "photos": [_photo_out(p) for p in photos],
        "curated": [p["i"] for p in photos if p["cid"] in curated_ids],
    }


def render(payload: dict[str, Any], template: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("{{ATLAS_DATA}}", data, 1)


def write_page(out_path: str | Path, rows: list[dict], stats: dict[str, Any], trips: list[dict],
               photos_by_checkin: dict[str, list[str]], tips: list[dict], config_dir: str | Path,
               ctry_norm: dict[str, str] | None = None, pix_url: str = "") -> dict[str, Any]:
    """build.py entry point. Returns the payload meta (for logging)."""
    config_dir = Path(config_dir)
    cfg = json.loads((config_dir / "travel_atlas.json").read_text(encoding="utf-8"))
    flags = json.loads((config_dir / "country_flags.json").read_text(encoding="utf-8"))
    pix = (pix_url.rstrip("/") + "/") if pix_url.startswith("http") else DEFAULT_PIX
    payload = build_payload(rows, stats, trips, photos_by_checkin, tips, cfg, flags, ctry_norm, pix)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload, TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")
    return payload["meta"]
