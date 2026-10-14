# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""Travel atlas journal — one standalone HTML page, read like a magazine.

Chapters instead of an endless feed: cover, world atlas, a spread per country,
a spread per trip, a year-by-year chronicle, a paged photo archive, a place
explorer and next-trip ideas. Everything (data + JS + CSS) is inlined, so the
file opens straight from disk on a phone.

Standalone on purpose: build.py and CI do not call it, and it only READS its
inputs. Rows go through the same `transform.apply_transforms` pipeline as the
site (plus the sibling backfill.csv), so every number matches 4sq.pages.dev.

    python scripts/gen_travel_atlas.py \\
      --input  C:/Users/toouur/Documents/GitHub/foursquare-data/checkins.csv \\
      --trips  _site/trips_meta.json \\
      --out    _site/travel-atlas.html

`--trips` should be the trips_meta.json written by a fresh build.py run (its
`--trips-out`); the copy committed to the data repo can lag behind.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from transform import apply_transforms, build_blank_city_resolver, load_mappings

log = logging.getLogger("gen_travel_atlas")

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "travel_atlas.html.tmpl"
DEFAULT_PIX = "https://pub-5514667a5da04a75986022e39efc7118.r2.dev/pix/"
PIX_PREFIX = "29447180_"           # common R2 key prefix, stripped to save ~200 KB
PRIVATE = {"Home (private)"}       # never surfaces in places / photos
EPOCH = date(2000, 1, 1)           # compact dates: days since EPOCH
SIGHT = re.compile(
    r"Monument|Plaza|Historic|Palace|Castle|Church|Cathedral|Mosque|Temple|Bridge|Beach|"
    r"Scenic|Lookout|Mountain|Lake|River|Harbor|Marina|Museum|Garden|Park|Waterfront|"
    r"Landmark|Fountain|Square|Tower|Street|Neighborhood|Island|Canal|Pier|Lighthouse|Fort|"
    r"Ruin|Shrine|Synagogue|Basilica|Hill|Nature|Waterfall|Canyon|Desert|Sculpture|Memorial|"
    r"Gate|Village|Town|City|Capitol|Pedestrian", re.I)


def _dn(day: str) -> int:
    return (date.fromisoformat(day) - EPOCH).days


def _km(a: float, b: float, c: float, d: float) -> float:
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _pkey(r: dict) -> str:
    return r.get("venue_id") or ("n:" + r.get("venue", "") + "|" + r.get("city", ""))


def _prepare(rows: list[dict], ctry_norm: dict[str, str]) -> list[dict]:
    for r in rows:
        r["country"] = ctry_norm.get(r.get("country", ""), r.get("country", "")).strip()
        r["ts"] = int(r.get("date") or 0)
        r["day"] = datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m-%d")
        try:
            r["la"], r["ln"] = float(r["lat"]), float(r["lng"])
        except (KeyError, TypeError, ValueError):
            r["la"] = r["ln"] = None
        if r["la"] == 0 and r["ln"] == 0:
            r["la"] = r["ln"] = None
    rows.sort(key=lambda r: r["ts"])
    return rows


def _pick(ph: list[list], k: int) -> list[list]:
    """Diverse highlight pick: best score first, one per venue, then by time."""
    seen: set[int] = set()
    out: list[list] = []
    for p in sorted(ph, key=lambda x: (-x[4], x[3])):
        if p[1] in seen:
            continue
        seen.add(p[1])
        out.append(p)
        if len(out) >= k:
            break
    return sorted(out, key=lambda x: x[3])


def build_payload(rows: list[dict], photos_json: dict[str, list[str]], trips: list[dict],
                  cfg: dict[str, Any], flags: dict[str, str], n_backfill: int = 0,
                  pix_url: str = DEFAULT_PIX) -> dict[str, Any]:
    """rows: transformed check-ins (country already canonical, `_prepare`d)."""
    iso_num: dict[str, str] = cfg.get("iso_numeric", {})
    curated_ids = set(cfg.get("curated_checkins", []))
    audit: dict[str, dict] = cfg.get("landmark_audit", {})

    cnt = Counter(r["country"] for r in rows if r["country"])
    countries = [c for c, _ in cnt.most_common()]
    cidx = {c: i for i, c in enumerate(countries)}
    catlist = [c for c, _ in Counter(r["category"] for r in rows
                                     if r.get("category") and r["category"] not in PRIVATE).most_common()]
    catidx = {c: i for i, c in enumerate(catlist)}

    # ---- places (unique venues) ----
    pl: dict[str, dict] = {}
    for r in rows:
        if r.get("category") in PRIVATE or not r["country"]:
            continue
        p = pl.get(_pkey(r))
        if p is None:
            p = pl[_pkey(r)] = {"cat": r.get("category", ""), "id": r.get("venue_id", ""),
                                "n": 0, "first": r["day"], "la": None, "ln": None}
        p["n"] += 1
        p["last"] = r["day"]
        p["name"] = r.get("venue") or p.get("name", "")
        p["city"] = r.get("city") or p.get("city", "")
        p["country"] = r["country"]
        if r["la"] is not None:
            p["la"], p["ln"] = r["la"], r["ln"]
    keys = sorted(pl, key=lambda k: -pl[k]["n"])
    pidx = {k: i for i, k in enumerate(keys)}

    # ---- photos: [file, placeIdx, day, ts, score, checkin_id] ----
    photos: list[list] = []
    for r in rows:
        files = photos_json.get(r.get("checkin_id", ""))
        if not files or r.get("category") in PRIVATE or _pkey(r) not in pidx:
            continue
        score = (3 if r["checkin_id"] in curated_ids else 0) + (1 if SIGHT.search(r.get("category") or "") else 0)
        for f in files:
            photos.append([f, pidx[_pkey(r)], r["day"], r["ts"], score, r["checkin_id"]])
    phidx = {id(p): i for i, p in enumerate(photos)}
    ph_by_country: dict[str, list[list]] = defaultdict(list)
    for p in photos:
        ph_by_country[pl[keys[p[1]]]["country"]].append(p)

    # ---- countries ----
    crow: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["country"]:
            crow[r["country"]].append(r)
    cout = []
    for c in countries:
        rs = crow[c]
        city_c = Counter(r["city"] for r in rs if r.get("city"))
        cities = []
        for city, n in city_c.most_common(8):
            pts = sorted((r["la"], r["ln"]) for r in rs if r["city"] == city and r["la"] is not None)
            mid = pts[len(pts) // 2] if pts else (None, None)
            cities.append([city, n,
                           round(mid[0], 4) if mid[0] is not None else None,
                           round(mid[1], 4) if mid[1] is not None else None])
        venues = Counter(_pkey(r) for r in rs if r.get("category") not in PRIVATE)
        days = {r["day"] for r in rs}
        cout.append({
            "en": c, "iso2": flags.get(c, ""), "num": iso_num.get(c, ""),
            "n": len(rs), "v": len({_pkey(r) for r in rs}), "c": len(city_c), "d": len(days),
            "first": rs[0]["day"], "last": rs[-1]["day"], "years": len({d[:4] for d in days}),
            "cities": cities,
            "venues": [[pl[k]["name"], n] for k, n in venues.most_common(8) if k in pl],
            "cats": [[k, n] for k, n in Counter(r["category"] for r in rs if r.get("category")
                                              and r["category"] not in PRIVATE).most_common(5)],
            "ph": [phidx[id(p)] for p in _pick(ph_by_country[c], 9)], "phn": len(ph_by_country[c]),
            "audit": audit.get(c),
        })

    # ---- trips ----
    ph_ts = [p[3] for p in photos]
    tout = []
    for t in sorted(trips, key=lambda t: t["start_ts"]):
        end = int(datetime.strptime(t["end_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 30 * 3600
        lo, hi = bisect.bisect_left(ph_ts, t["start_ts"]), bisect.bisect_right(ph_ts, end)
        tc = set(t["countries"])
        tph = [p for p in photos[lo:hi] if pl[keys[p[1]]]["country"] in tc]
        tout.append({"id": t["id"], "name": t["name"], "s": t["start_date"], "e": t["end_date"],
                     "dur": t["duration"], "n": t["checkin_count"], "u": t["unique_places"],
                     "co": t["countries"], "ci": t["cities"][:14], "cin": len(t["cities"]),
                     "ph": [phidx[id(p)] for p in _pick(tph, 9)], "phn": len(tph)})

    # ---- years ----
    seen_c: set[str] = set()
    byy: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        byy[r["day"][:4]].append(r)
    ph_year = Counter(p[2][:4] for p in photos)
    tr_year = Counter(t["start_date"][:4] for t in trips)
    years = []
    for y in sorted(byy):
        new = []
        for r in byy[y]:
            if r["country"] and r["country"] not in seen_c:
                seen_c.add(r["country"])
                new.append(r["country"])
        years.append({"y": y, "n": len(byy[y]), "co": len({r["country"] for r in byy[y] if r["country"]}),
                      "ci": len({r["city"] for r in byy[y] if r.get("city")}), "new": new,
                      "tr": tr_year[y], "ph": ph_year[y]})

    # ---- map cities + country-to-country moves ----
    ccount = Counter((r["city"], r["country"]) for r in rows if r.get("city") and r["country"] and r["la"] is not None)
    mapcities = []
    for (city, c), n in ccount.most_common(160):
        pts = sorted((r["la"], r["ln"]) for r in crow[c] if r["city"] == city and r["la"] is not None)
        m = pts[len(pts) // 2]
        mapcities.append([city, cidx[c], n, round(m[0], 3), round(m[1], 3)])
    routes: dict[tuple, list] = {}
    prev = None
    for r in rows:
        if not r["country"] or r["la"] is None:
            continue
        if prev and prev["country"] != r["country"]:
            k = (prev.get("city"), r.get("city"), r["day"][:4])
            if k not in routes:
                routes[k] = [round(prev["la"], 2), round(prev["ln"], 2), round(r["la"], 2), round(r["ln"], 2),
                             int(r["day"][:4]), cidx[prev["country"]], cidx[r["country"]],
                             prev.get("city", ""), r.get("city", ""), r["day"],
                             round(_km(prev["la"], prev["ln"], r["la"], r["ln"]))]
        prev = r

    return {
        "meta": {"checkins": len(rows), "refurb": n_backfill, "countries": len(countries),
                 "cities": len({(r["city"], r["country"]) for r in rows if r.get("city")}),
                 "venues": len({_pkey(r) for r in rows}), "photos": len(photos),
                 "photoCheckins": len({p[5] for p in photos}), "trips": len(trips),
                 "first": rows[0]["day"] if rows else "", "last": rows[-1]["day"] if rows else "",
                 "days": len({r["day"] for r in rows}), "built": date.today().isoformat(), "pix": pix_url},
        "C": countries, "K": catlist, "countries": cout, "trips": tout, "years": years,
        "cities": mapcities, "routes": list(routes.values()),
        # place: [name, city, countryIdx, lat, lng, n, first (0 = same as last), last, catIdx, venueId]
        "places": [[pl[k]["name"], pl[k]["city"], cidx[pl[k]["country"]],
                    round(pl[k]["la"], 4) if pl[k]["la"] is not None else None,
                    round(pl[k]["ln"], 4) if pl[k]["ln"] is not None else None,
                    pl[k]["n"], 0 if pl[k]["first"] == pl[k]["last"] else _dn(pl[k]["first"]),
                    _dn(pl[k]["last"]), catidx.get(pl[k]["cat"], -1), pl[k]["id"]] for k in keys],
        # photo: [file ('!' = full key, else PIX_PREFIX stripped), placeIdx, day]
        "photos": [[p[0][len(PIX_PREFIX):] if p[0].startswith(PIX_PREFIX) else "!" + p[0], p[1], _dn(p[2])]
                   for p in photos],
        "curated": [i for i, p in enumerate(photos) if p[5] in curated_ids],
    }


def render(payload: dict[str, Any], template: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("{{ATLAS_DATA}}", data, 1)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="checkins.csv (a sibling backfill.csv is merged)")
    ap.add_argument("--photos", default=None, help="photos.json (default: sibling of --input)")
    ap.add_argument("--trips", default=None,
                    help="trips_meta.json from build.py --trips-out (default: ./trips_meta.json, "
                         "then sibling of --input)")
    ap.add_argument("--config-dir", default=str(ROOT / "config"))
    ap.add_argument("--pix-url", default=DEFAULT_PIX)
    ap.add_argument("--out", default="_site/travel-atlas.html")
    args = ap.parse_args()

    from gen_tips import CTRY_NORM  # loads config/country_aliases.json

    src = Path(args.input).resolve()
    cfg_dir = Path(args.config_dir)
    with open(src, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    n_bf = 0
    bf = src.parent / "backfill.csv"
    if bf.exists():
        with open(bf, encoding="utf-8") as fh:
            bf_rows = list(csv.DictReader(fh))
        rows.extend(bf_rows)
        n_bf = len(bf_rows)
    rows.sort(key=lambda r: int(r.get("date") or 0))
    rows = apply_transforms(rows, load_mappings(cfg_dir),
                            blank_city_resolver=build_blank_city_resolver(cfg_dir / "city_merge_normalized_review.csv"))
    rows = _prepare(rows, CTRY_NORM)

    photos_path = Path(args.photos) if args.photos else src.parent / "photos.json"
    photos_json = json.loads(photos_path.read_text(encoding="utf-8")) if photos_path.exists() else {}
    trips_path = Path(args.trips) if args.trips else next(
        (p for p in (Path("trips_meta.json"), src.parent / "trips_meta.json") if p.exists()), None)
    trips = json.loads(trips_path.read_text(encoding="utf-8")) if trips_path else []
    if not trips:
        log.warning("no trips_meta.json found — the trips chapter will be empty")

    cfg = json.loads((cfg_dir / "travel_atlas.json").read_text(encoding="utf-8"))
    flags = json.loads((cfg_dir / "country_flags.json").read_text(encoding="utf-8"))
    payload = build_payload(rows, photos_json, trips, cfg, flags, n_bf, args.pix_url)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload, TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")
    m = payload["meta"]
    log.info("travel atlas → %s (%.1f MB): %d check-ins, %d countries, %d trips, %d photos",
             out, out.stat().st_size / 1e6, m["checkins"], m["countries"], m["trips"], m["photos"])


if __name__ == "__main__":
    main()
