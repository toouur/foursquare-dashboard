# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""build_backfill.py — Convert reconstructed pre-2012 travel history into a
separate check-in CSV in the exact Foursquare 23-column schema.

The Foursquare/Swarm dataset only starts in 2012, but real travel history goes
back further. Reconstructed visits (from Polarsteps exports, the LiveInternet
diary, or plain memory) live in a human-editable intermediate file
``backfill.yaml`` next to ``checkins.csv`` in the data repo. This converter reads
that file and emits ``backfill.csv``, which ``build.py`` merges into the main row
set (so every KPI/story/map shifts) and ``sync_to_d1.py`` ingests (so the live
feed + search return them too).

Each reconstructed row is stamped ``source_app = "refurbished"`` — the marker the
rest of the pipeline uses to badge it as reconstructed. Real rows only ever carry
"Swarm for Android/iOS", "Foursquare for Android", or "Pebble", so there is no
collision. Check-in ids are ``rf`` + a zero-padded sequence (``rf0001``), which
never collides with the real 24-char hex ids and is ignored by the
city_fixes/venue_fixes gates.

backfill.yaml entry shape (coarse granularity is fine):

    - date: 2009-07-14      # or "2009-07" (month) or "2009" (year)
      city: Prague
      country: Czechia
      venue: ""             # optional; usually blank
      lat: 50.08            # optional explicit coords (override centroid lookup)
      lng: 14.44
      source: liveinternet  # provenance note: manual | liveinternet | polarsteps
      note: "Interrail, first night"   # optional -> becomes the shout text

Coordinate resolution is fully offline and deterministic:
  1. explicit ``lat``/``lng`` on the entry, else
  2. the mean centroid of that city already present in ``checkins.csv`` (exact
     normalized-name match — guaranteed consistent with the existing map layers),
     else
  3. a bundled gazetteer ``config/backfill_gazetteer.json`` for places that never
     appear post-2012.
An entry that resolves to none of these is reported and skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("build_backfill")

# The canonical Foursquare check-in CSV header, in order.
SCHEMA = [
    "date", "venue", "venue_id", "venue_url", "city", "state", "country",
    "neighborhood", "lat", "lng", "address", "category", "shout", "source_app",
    "source_url", "with_name", "with_id", "created_by_name", "created_by_id",
    "overlaps_name", "overlaps_id", "city_inferred", "checkin_id",
]

# The sentinel that marks a row as reconstructed everywhere downstream.
REFURB_APP = "refurbished"


def _norm_city(name: str) -> str:
    """NFC-normalize + strip a city name for consistent matching.

    Mirrors transform.py, which NFC-normalizes every city before any string-keyed
    rule runs — so a diacritic city here matches the same city in checkins.csv.
    """
    return unicodedata.normalize("NFC", (name or "").strip())


def parse_backfill_date(value: Any) -> int:
    """Resolve a coarse date to a unix timestamp at 12:00 UTC.

    Accepts a ``datetime.date``/``datetime`` (PyYAML parses ``2009-07-14`` to a
    date) or a string ``"YYYY-MM-DD"`` / ``"YYYY-MM"`` / ``"YYYY"``. A month-only
    value fills day 1; a year-only value fills July 1 (mid-year).
    """
    if isinstance(value, datetime):
        y, mo, d = value.year, value.month, value.day
    elif isinstance(value, date):
        y, mo, d = value.year, value.month, value.day
    else:
        s = str(value).strip()
        parts = s.split("-")
        try:
            if len(parts) >= 3:
                y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
            elif len(parts) == 2:
                y, mo, d = int(parts[0]), int(parts[1]), 1
            elif len(parts) == 1 and parts[0]:
                y, mo, d = int(parts[0]), 7, 1
            else:
                raise ValueError("empty date")
        except ValueError as e:
            raise ValueError(f"unparseable backfill date {value!r}: {e}") from None
    return int(datetime(y, mo, d, 12, 0, 0, tzinfo=timezone.utc).timestamp())


def load_centroids(csv_path: str | Path) -> dict[str, tuple[float, float]]:
    """Mean (lat, lng) per normalized city name from an existing check-in CSV.

    Reusing the real centroid keeps a reconstructed row on top of the same map
    cluster the city already occupies. Keyed by casefolded NFC name for a
    forgiving match. Returns an empty map if the CSV is missing.
    """
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    p = Path(csv_path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            city = _norm_city(row.get("city", ""))
            if not city:
                continue
            try:
                lat = float(row["lat"]); lng = float(row["lng"])
            except (KeyError, ValueError, TypeError):
                continue
            a = acc[city.casefold()]
            a[0] += lat; a[1] += lng; a[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in acc.items() if v[2]}


def load_gazetteer(path: str | Path) -> dict[str, tuple[float, float]]:
    """Load the bundled city→(lat,lng) fallback gazetteer, casefold-keyed."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    out: dict[str, tuple[float, float]] = {}
    for city, coords in raw.items():
        if city.startswith("_"):   # skip "_comment" / doc keys
            continue
        try:
            out[_norm_city(city).casefold()] = (float(coords[0]), float(coords[1]))
        except (TypeError, ValueError, IndexError):
            log.warning("gazetteer entry %r is malformed — skipped", city)
    return out


def resolve_coords(
    entry: dict,
    centroids: dict[str, tuple[float, float]],
    gazetteer: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """Resolve an entry's (lat, lng): explicit → centroid → gazetteer → None."""
    lat, lng = entry.get("lat"), entry.get("lng")
    if lat is not None and lng is not None:
        try:
            return (float(lat), float(lng))
        except (TypeError, ValueError):
            log.warning("entry for %r has non-numeric lat/lng — ignored",
                        entry.get("city"))
    key = _norm_city(entry.get("city", "")).casefold()
    if not key:
        return None
    if key in centroids:
        return centroids[key]
    if key in gazetteer:
        return gazetteer[key]
    return None


def entry_to_row(entry: dict, seq: int, coords: tuple[float, float]) -> dict:
    """Build one 23-column check-in row from a resolved backfill entry."""
    ts = parse_backfill_date(entry["date"])
    lat, lng = coords
    row = {k: "" for k in SCHEMA}
    row["date"] = str(ts)
    row["venue"] = _norm_city(str(entry.get("venue") or "")).strip()
    row["city"] = _norm_city(entry.get("city", ""))
    row["country"] = _norm_city(entry.get("country", ""))
    row["lat"] = f"{lat:.6f}"
    row["lng"] = f"{lng:.6f}"
    row["shout"] = str(entry.get("note") or "").strip()
    row["source_app"] = REFURB_APP
    # Provenance ("polarsteps"/"liveinternet"/"manual") rides in source_url so it
    # survives into the row without needing a schema column of its own.
    src = str(entry.get("source") or "manual").strip()
    row["source_url"] = f"backfill:{src}"
    row["city_inferred"] = "1"
    row["checkin_id"] = f"rf{seq:04d}"
    return row


def build_rows(
    entries: list[dict],
    centroids: dict[str, tuple[float, float]],
    gazetteer: dict[str, tuple[float, float]],
) -> tuple[list[dict], list[dict]]:
    """Convert entries → (rows, unresolved). Rows are date-sorted; ``rf`` ids are
    assigned in that stable order so a given backfill.yaml always yields the same
    ids. Entries missing a date/city or with unresolvable coords are collected in
    ``unresolved`` (reported, not emitted)."""
    resolved: list[tuple[int, dict, tuple[float, float]]] = []
    unresolved: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("date") or not entry.get("city"):
            unresolved.append({"entry": entry, "why": "missing date or city"})
            continue
        try:
            ts = parse_backfill_date(entry["date"])
        except ValueError as e:
            unresolved.append({"entry": entry, "why": str(e)})
            continue
        coords = resolve_coords(entry, centroids, gazetteer)
        if coords is None:
            unresolved.append({"entry": entry,
                               "why": f"no coords for city {entry.get('city')!r}"})
            continue
        resolved.append((ts, entry, coords))

    resolved.sort(key=lambda t: (t[0], _norm_city(t[1].get("city", ""))))
    rows = [entry_to_row(entry, i + 1, coords)
            for i, (_ts, entry, coords) in enumerate(resolved)]
    return rows, unresolved


def write_csv(rows: list[dict], out_path: str | Path) -> None:
    """Write rows to a CSV with the canonical schema header."""
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCHEMA)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert backfill.yaml → backfill.csv")
    ap.add_argument("--yaml", required=True, help="path to backfill.yaml")
    ap.add_argument("--checkins", required=True,
                    help="path to checkins.csv (for centroid reuse)")
    ap.add_argument("--gazetteer",
                    default=str(Path(__file__).resolve().parent.parent
                               / "config" / "backfill_gazetteer.json"),
                    help="path to the fallback city→latlng gazetteer JSON")
    ap.add_argument("--out", required=True, help="path to write backfill.csv")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with open(args.yaml, encoding="utf-8") as fh:
        entries = yaml.safe_load(fh) or []
    if not isinstance(entries, list):
        log.error("backfill.yaml must be a YAML list of entries")
        return 1

    centroids = load_centroids(args.checkins)
    gazetteer = load_gazetteer(args.gazetteer)
    rows, unresolved = build_rows(entries, centroids, gazetteer)

    write_csv(rows, args.out)
    log.info("Wrote %d reconstructed check-in(s) → %s", len(rows), args.out)
    if unresolved:
        log.warning("%d entr(y/ies) could not be resolved:", len(unresolved))
        for u in unresolved:
            log.warning("  - %s (%s)", u["entry"], u["why"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
