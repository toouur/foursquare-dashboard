# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
culture_sources.py — read the cultural exports that sit next to checkins.csv.

* ``imdb_export.csv``     IMDb → Your Ratings → Export. Every rated title; the
                           date is the RATING date, not the viewing date. A day
                           with ``BULK_DAY`` or more ratings is a catch-up session
                           (marking films seen long ago) and is flagged ``bulk``.
* ``full_wine_list.csv``  Vivino data export. Only wines carrying "Your rating"
                           are kept (the export also lists wines merely scanned or
                           wish-listed). ``Scan date`` is UTC — checked against
                           check-ins: scan-minus-nearest-bar/restaurant-check-in
                           peaks at 0 h when read as UTC, at +2/+3 h it would be
                           local. Exact duplicate rows (same wine, vintage and
                           scan second) are collapsed.
* ``lastfm/``              the raw scrobble layer written by fetch_lastfm.py.

All loaders return [] for a missing file, so the build degrades to "no culture".
"""
from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BULK_DAY = 10


def _num(v, cast=float):
    try:
        return cast(str(v).strip())
    except (TypeError, ValueError):
        return None


def load_imdb(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rating = _num(r.get("Your Rating"), int)
            date = (r.get("Date Rated") or "").strip()[:10]
            if rating is None or len(date) != 10:
                continue
            out.append({
                "id": (r.get("Const") or "").strip(),
                "title": (r.get("Title") or "").strip(),
                "original": (r.get("Original Title") or "").strip(),
                "rating": rating,
                "imdb_rating": _num(r.get("IMDb Rating")),
                "date": date,
                "type": (r.get("Title Type") or "").strip(),
                "year": _num(r.get("Year"), int),
                "genres": [g.strip() for g in (r.get("Genres") or "").split(",") if g.strip()],
                "runtime": _num(r.get("Runtime (mins)"), int),
                "directors": (r.get("Directors") or "").strip(),
                "url": (r.get("URL") or "").strip(),
            })
    per_day = Counter(f["date"] for f in out)
    for f in out:
        f["bulk"] = per_day[f["date"]] >= BULK_DAY
    out.sort(key=lambda f: (f["date"], f["title"]))
    log.info("IMDb: %d rated titles (%d on catch-up days)", len(out), sum(f["bulk"] for f in out))
    return out


def load_vivino(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rating = _num(r.get("Your rating"))
            if rating is None:
                continue                      # scanned or wish-listed, never rated
            scan = (r.get("Scan date") or "").strip()
            ts = None
            if scan:
                try:
                    ts = int(datetime.strptime(scan, "%Y-%m-%d %H:%M:%S")
                             .replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    log.warning("Vivino: unreadable scan date %r", scan)
            key = ((r.get("Link to wine") or "").strip(), (r.get("Vintage") or "").strip(), scan)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "wine": (r.get("Wine name") or "").strip(),
                "winery": (r.get("Winery") or "").strip(),
                "vintage": (r.get("Vintage") or "").strip(),
                "region": (r.get("Region") or "").strip(),
                "country": (r.get("Country") or "").strip(),
                "style": (r.get("Regional wine style") or "").strip(),
                "type": (r.get("Wine type") or "").strip(),
                "rating": rating,
                "avg": _num(r.get("Average rating")),
                "ts": ts,
                "review": (r.get("Your review") or "").strip(),
                "url": (r.get("Link to wine") or "").strip(),
                "label": (r.get("Label image") or "").strip(),
            })
    out.sort(key=lambda w: (w["ts"] is None, w["ts"] or 0))
    log.info("Vivino: %d rated wines (%d without a scan date)", len(out), sum(w["ts"] is None for w in out))
    return out


def load_scrobbles(dir_: str | Path) -> list[tuple[int, str, str, str]]:
    """[(ts, artist, track, album)] from lastfm/scrobbles_<YYYY>.tsv, oldest first."""
    d = Path(dir_)
    if not d.is_dir():
        return []
    from fetch_lastfm import load_raw   # the writer owns the format
    raw = load_raw(d)
    out = sorted((r["ts"], r["artist"], r["track"], r["album"]) for rows in raw.values() for r in rows.values())
    log.info("Last.fm raw layer: %d scrobbles", len(out))
    return out
