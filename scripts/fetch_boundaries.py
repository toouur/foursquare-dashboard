#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""fetch_boundaries.py — cache the OpenStreetMap outline of every country/city page.

The country and city pages draw the place's outline over their map. Fetching it
from Nominatim in the visitor's browser makes every first visit depend on a free
service with a strict usage policy (1 request/s, no heavy use), so the outline
is fetched here instead, once per place, and kept in the data repo's
boundaries.json:

    {"city|Minsk|Belarus": {"g": <GeoJSON Polygon/MultiPolygon> | null, "t": <unix ts>}}

build.py --boundaries hands the cache to gen_country_pages, which writes one
bnd_<kind>_<slug>.json per page; a page without an entry still looks the
outline up live, as before. A place Nominatim has no polygon for is stored as
null and retried after RETRY_DAYS.

Usage:
    python scripts/fetch_boundaries.py --csv $DATA/checkins.csv --backfill $DATA/backfill.csv \\
        --cache $DATA/boundaries.json [--max 400]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

URL = "https://nominatim.openstreetmap.org/search"
UA = "4sq-dashboard boundaries (+https://github.com/toouur/foursquare-dashboard)"
RETRY_DAYS = 90
# Same simplification the page used live: a city keeps its streets-level edge,
# a country only its shape (Russia at 0.0005 would be megabytes).
THRESHOLD = {"city": 0.0005, "country": 0.01}
DIGITS = {"city": 5, "country": 3}


def _round(coords, nd: int):
    if isinstance(coords, (int, float)):
        return round(coords, nd)
    return [_round(c, nd) for c in coords]


# Free-text fallback: the admin levels that can be "the city". A hamlet or a
# reservoir that shares the name is not (Copenhagen, Zaslawye both have one).
SETTLEMENT_LEVELS = {"city", "town", "village", "municipality"}
AREA_LEVELS = {"state", "county", "province", "region", "district"}
AREA_WORDS = ("region", "district", "rayon", "rajonas", "oblast")


def _polygon(hit: dict, kind: str) -> dict | None:
    g = hit.get("geojson")
    if not g or "Polygon" not in (g.get("type") or ""):
        return None
    return {"type": g["type"], "coordinates": _round(g["coordinates"], DIGITS[kind])}


def _covers(hit: dict, at: tuple[float, float] | None) -> bool:
    if at is None:
        return True
    try:
        s, n, w, e = map(float, hit.get("boundingbox") or [])
    except ValueError:
        return False
    return s <= at[0] <= n and w <= at[1] <= e


def fetch_one(session: requests.Session, kind: str, name: str, country: str,
              at: tuple[float, float] | None = None) -> dict | None:
    """The outline of a page's place, or None. A structured city search often
    lands on the city's point node (Copenhagen, Bled); then a free-text search
    takes the first administrative polygon of a settlement level (an area level
    for "… Region" / "… District" names) whose box holds the check-ins' centre."""
    params: dict[str, str | int | float] = {"format": "jsonv2", "limit": 1, "polygon_geojson": 1,
                                            "polygon_threshold": THRESHOLD[kind]}
    if kind == "city":
        params.update(city=name, country=country)
    else:
        params["country"] = name
    r = session.get(URL, params=params, timeout=30)
    r.raise_for_status()
    hits = r.json()
    g = _polygon(hits[0], kind) if hits else None
    if g or kind != "city":
        return g
    time.sleep(1.1)
    params = {"format": "jsonv2", "limit": 8, "polygon_geojson": 1, "polygon_threshold": THRESHOLD[kind],
              "q": f"{name}, {country}"}
    r = session.get(URL, params=params, timeout=30)
    r.raise_for_status()
    levels = SETTLEMENT_LEVELS | (AREA_LEVELS if any(w in name.lower() for w in AREA_WORDS) else set())
    for hit in r.json():
        if hit.get("category") == "boundary" and hit.get("addresstype") in levels and _covers(hit, at):
            return _polygon(hit, kind)
    return None


def load_rows(csv_path: str, backfill: str | None, config_dir: str) -> list[dict]:
    from transform import apply_transforms, build_blank_city_resolver, load_mappings
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if backfill and Path(backfill).exists():
        with open(backfill, encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    resolver = build_blank_city_resolver(Path(config_dir) / "city_merge_normalized_review.csv")
    return apply_transforms(rows, load_mappings(config_dir), blank_city_resolver=resolver)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--backfill")
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--cache", required=True, help="boundaries.json (created when missing)")
    ap.add_argument("--max", type=int, default=400, help="Max places to look up this run")
    ap.add_argument("--retry-missing", action="store_true",
                    help="Look up every place cached without an outline again, now")
    args = ap.parse_args()

    from gen_country_pages import bnd_key, page_places

    cache_path = Path(args.cache)
    cache: dict = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    now = int(time.time())
    retry_before = now if args.retry_missing else now - RETRY_DAYS * 86400
    rows = load_rows(args.csv, args.backfill, args.config_dir)
    todo = [p for p in page_places(rows)
            if bnd_key(*p) not in cache
            or (cache[bnd_key(*p)].get("g") is None and cache[bnd_key(*p)].get("t", 0) < retry_before)]
    acc: dict[str, list[float]] = {}
    for r in rows:
        try:
            la, lo = float(r["lat"]), float(r["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        a = acc.setdefault((r.get("city") or "").strip(), [0.0, 0.0, 0])
        a[0] += la
        a[1] += lo
        a[2] += 1
    centre = {c: (a[0] / a[2], a[1] / a[2]) for c, a in acc.items() if c and a[2]}
    print(f"{len(todo)} place(s) without an outline; fetching up to {args.max}")

    session = requests.Session()
    session.headers["User-Agent"] = UA
    got = missing = failed = 0
    for i, (kind, name, country) in enumerate(todo[:args.max]):
        if i:
            time.sleep(1.1)                      # Nominatim policy: at most 1 request/s
        try:
            g = fetch_one(session, kind, name, country, centre.get(name) if kind == "city" else None)
        except (requests.RequestException, ValueError) as e:
            print(f"  ! {kind} {name} ({country}): {e}")
            failed += 1
            if failed >= 5:
                print("  five failures — stopping; the next run continues")
                break
            continue
        cache[bnd_key(kind, name, country)] = {"g": g, "t": now}
        if g:
            got += 1
        else:
            missing += 1
            print(f"  – no polygon: {kind} {name} ({country})")
        if (i + 1) % 25 == 0:                    # a cancelled run keeps what it fetched
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                                  encoding="utf-8")

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                          encoding="utf-8")
    print(f"Outlines: +{got} found, {missing} without a polygon, {failed} failed; "
          f"{len(cache)} cached in {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
