#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""refresh_city_review.py — add new cities to the blank-city review table.

A check-in with a blank city takes the city of the nearest point in
config/city_merge_normalized_review.csv. That table is a snapshot (March 2026),
so a city first visited later is not in it, and its blank rows land in whatever
reviewed city is within reach (Liège -> Charleroi, Amsterdam -> Rotterdam).

This script finds cities your real check-ins name (>= MIN_CHECKINS rows, after
the full transform) that the table does not know, and appends one point per
city: the median of those check-ins, review AUTO_ADDED. Reviewed rows are never
touched.

Only cities with no reviewed point within SEP_KM are added. A city closer than
that is a suburb next to a reviewed one (Valeryanovo, Pryluky next to Minsk):
its point would pull the reviewed city's own blank rows, because Foursquare
calls the suburbs "Minsk" too. Those stay with the live rule in transform.py
(a real check-in of the city within 3 km) and are listed as "too close".

Usage:
    python scripts/refresh_city_review.py --csv $DATA/checkins.csv --backfill $DATA/backfill.csv          # report
    python scripts/refresh_city_review.py --csv … --backfill … --write                                  # append
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import transform as t  # noqa: E402

MIN_CHECKINS = t.LIVE_CITY_MIN
SEP_KM = t.LIVE_REVIEW_FAR_KM


@dataclass
class Missing:
    country: str
    city: str
    checkins: int
    lat: float
    lng: float
    nearest_km: float
    nearest_city: str

    @property
    def addable(self) -> bool:
        return self.nearest_km >= SEP_KM


def load_rows(csv_path: str, backfill: str | None, config_dir: str) -> list[dict]:
    """checkins (+ backfill) through the same transform the build runs."""
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if backfill and Path(backfill).exists():
        with open(backfill, encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
        rows.sort(key=lambda r: int(r.get("date") or 0))
    review = Path(config_dir) / t.REVIEW_CSV_NAME
    return t.apply_transforms(rows, t.load_mappings(config_dir),
                              blank_city_resolver=t.build_blank_city_resolver(review))


def missing_cities(rows: list[dict], review_csv: str | Path) -> list[Missing]:
    """Cities real check-ins name that the review table lacks, nearest-first by country."""
    _, centroids, reviewed = t.review_points(review_csv)
    pts: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        city = r.get("city") or ""
        if not city or r.get("city_inferred") == "1":
            continue
        if (r.get("category") or "").strip() in t._COUNTRY_LEVEL:
            continue
        try:
            pts[(r.get("country", ""), city)].append((float(r["lat"]), float(r["lng"])))
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for (country, city), p in pts.items():
        if city in reviewed.get(country, set()) or len(p) < MIN_CHECKINS:
            continue
        lat = statistics.median(a for a, _ in p)
        lng = statistics.median(b for _, b in p)
        near = min(((t._haversine(lat, lng, a, b), c) for a, b, c in centroids.get(country, [])),
                   default=(float("inf"), ""))
        out.append(Missing(country, city, len(p), lat, lng, near[0], near[1]))
    out.sort(key=lambda m: (m.country, -m.checkins, m.city))
    return out


def append_rows(review_csv: str | Path, add: list[Missing], today: str) -> None:
    review_csv = Path(review_csv)
    with open(review_csv, encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    with open(review_csv, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        for m in add:
            w.writerow({
                "raw_city": f"(AUTO) [{m.country}] -> {m.city}",
                "country": m.country,
                "checkins_count": m.checkins,
                "all_raw_count": 0,
                "lat": f"{m.lat:.6f}",
                "lng": f"{m.lng:.6f}",
                "low_volume_checkins_count": 0,
                "normalized_name": m.city.lower(),
                "decision_source": "BLANK_CITY_INFERRED",
                "suggested_target": m.city,
                "resolved_city": m.city,
                "distance_to_target_km": 0,
                "review": "AUTO_ADDED",
                "comment": (f"refresh_city_review.py {today}: median of {m.checkins} real check-ins; "
                            f"nearest reviewed point {m.nearest_city} {m.nearest_km:.0f} km"),
            })


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--backfill")
    ap.add_argument("--config-dir", default="config")
    ap.add_argument("--write", action="store_true", help="Append the addable cities to the review CSV")
    args = ap.parse_args()

    review = Path(args.config_dir) / t.REVIEW_CSV_NAME
    miss = missing_cities(load_rows(args.csv, args.backfill, args.config_dir), review)
    add = [m for m in miss if m.addable]
    close = [m for m in miss if not m.addable]
    print(f"{len(add)} cities to add, {len(close)} too close to a reviewed one (left to the live rule)")
    for m in add:
        print(f"  + {m.country} | {m.city} | {m.checkins} check-ins | nearest reviewed {m.nearest_city} {m.nearest_km:.0f} km")
    for m in close:
        print(f"    {m.country} | {m.city} | {m.checkins} | {m.nearest_city} {m.nearest_km:.1f} km")
    if args.write and add:
        append_rows(review, add, date.today().isoformat())
        print(f"Appended {len(add)} rows to {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
