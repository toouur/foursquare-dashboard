# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""import_polarsteps.py — Turn a Polarsteps "Download my data" export into
reviewable ``backfill.yaml`` entries for the pre-2012 travel backfill.

Polarsteps has no usable public API (versioned clients only, HTTP 410), so the
account **data export** is the sole route. The export is a directory tree::

    user_data/
      user/user.json
      trip/<slug>_<id>/trip.json     # 135 trips
                       locations.json

Each ``trip.json`` carries ``all_steps`` — one step per place, each with
``start_time`` (unix seconds, the travel time), ``location.{lat, lon, name,
country_code, detail}`` and a human ``display_name``. This script reads every
step, keeps the ones inside the window (``--before YEAR``, default 2012 — the
year the real Foursquare/Swarm data begins, so earlier steps fill a genuine gap
instead of duplicating real check-ins), collapses same-day repeats at one city,
and emits ``backfill.yaml`` entries in the shape ``build_backfill.py`` consumes.

It deliberately writes to a **separate review file** (default
``backfill_polarsteps.yaml``), NOT the live ``backfill.yaml`` — nothing reaches
the dashboard until the user reviews these entries, prunes them, and merges them
into ``backfill.yaml`` themselves (or re-runs with ``--out backfill.yaml
--append`` once satisfied). ``build.py``/``sync_to_d1.py`` only ever read the
generated ``backfill.csv``, which this script never touches.

City label: ``display_name`` is preferred over ``location.name`` because
Polarsteps often stores a raw administrative area in ``name`` (e.g.
"Гайненскі сельскі Савет") while ``display_name`` holds the friendly place
("Lahaza"). Country: the ISO-3166 ``country_code`` mapped through
``config/country_flags.json`` (canonical to the rest of the project), falling
back to the export's own English ``detail`` string.

Usage::

    python scripts/import_polarsteps.py \
      --export C:/Users/toouur/Downloads/user_data \
      --out    backfill_polarsteps.yaml           # review file (default)
    # After reviewing, merge the good entries into the data repo's backfill.yaml.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("import_polarsteps")

REFURB_SOURCE = "polarsteps"


def _norm(s: str) -> str:
    """NFC-normalize + strip, mirroring build_backfill / transform."""
    return unicodedata.normalize("NFC", (s or "").strip())


def load_iso2_map(flags_path: str | Path) -> dict[str, str]:
    """ISO-3166 alpha-2 (upper) → English country name, inverted from the
    project's canonical English→ISO2 ``country_flags.json`` (first name wins)."""
    p = Path(flags_path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        flags = json.load(fh)
    inv: dict[str, str] = {}
    for name, code in flags.items():
        inv.setdefault(str(code).upper(), name)
    return inv


def step_country(loc: dict, iso2: dict[str, str]) -> str:
    """Resolve a step's English country name: ISO2 map → export ``detail``."""
    code = str(loc.get("country_code") or "").upper()
    if code and code in iso2:
        return iso2[code]
    return _norm(str(loc.get("detail") or ""))


def step_city(step: dict, loc: dict) -> str:
    """Best city label: friendly ``display_name`` over the raw ``location.name``."""
    return _norm(str(step.get("display_name") or loc.get("name") or ""))


def iter_steps(export_dir: str | Path):
    """Yield ``(trip_name, step_dict)`` for every step across all trip.json files."""
    root = Path(export_dir)
    for trip_json in sorted(root.glob("trip/*/trip.json")):
        try:
            with open(trip_json, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("skipping unreadable %s: %s", trip_json, e)
            continue
        trip_name = _norm(str(d.get("name") or trip_json.parent.name))
        for step in (d.get("all_steps") or []):
            if isinstance(step, dict):
                yield trip_name, step


def build_entries(
    export_dir: str | Path,
    iso2: dict[str, str],
    before_year: int | None,
) -> tuple[list[dict], int, int]:
    """Build deduped backfill entries from the export.

    Returns ``(entries, total_steps, skipped_no_time)``. Steps at or after
    ``before_year`` are excluded (the Foursquare era covers them). Same-day
    repeats at one city collapse to a single entry (backfill granularity is
    country/city/day); the earliest step of the day at that city wins, and its
    coords/note are kept. Entries come back sorted by date then city.
    """
    total = 0
    skipped = 0
    # key: (date_str, casefolded city) -> entry (earliest ts kept)
    best: dict[tuple[str, str], dict] = {}
    best_ts: dict[tuple[str, str], float] = {}

    for trip_name, step in iter_steps(export_dir):
        total += 1
        ts = step.get("start_time")
        if not ts:
            skipped += 1
            continue
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        if before_year is not None and day.year >= before_year:
            continue
        loc = step.get("location") or {}
        city = step_city(step, loc)
        if not city:
            continue
        date_str = day.strftime("%Y-%m-%d")
        key = (date_str, city.casefold())
        if key in best_ts and best_ts[key] <= ts:
            continue  # keep the earliest step of the day at this city
        desc = _norm(str(step.get("description") or ""))
        entry: dict[str, Any] = {
            "date": date_str,
            "city": city,
            "country": step_country(loc, iso2),
            "source": REFURB_SOURCE,
            "note": desc or trip_name,
            "trip": trip_name,
        }
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is not None and lon is not None:
            entry["lat"] = round(float(lat), 6)
            entry["lng"] = round(float(lon), 6)
        sid = step.get("id")
        if sid is not None:
            entry["ps_id"] = sid
        best[key] = entry
        best_ts[key] = ts

    entries = sorted(best.values(), key=lambda e: (e["date"], e["city"].casefold()))
    return entries, total, skipped


# Field order for readable YAML entries (unknown keys append after).
_KEY_ORDER = ["date", "city", "country", "lat", "lng", "venue", "note",
              "source", "trip", "ps_id"]


def _ordered(entry: dict) -> dict:
    ordered = {k: entry[k] for k in _KEY_ORDER if k in entry}
    for k, v in entry.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def write_yaml(entries: list[dict], out_path: str | Path, append: bool) -> int:
    """Write entries to ``out_path`` as a YAML list.

    With ``append``, merge into an existing list, deduping by ``ps_id`` (so a
    re-run doesn't double an already-reviewed step). Returns the number of NEW
    entries written.
    """
    out = Path(out_path)
    existing: list[dict] = []
    if append and out.exists():
        with open(out, encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or []
        if not isinstance(existing, list):
            raise SystemExit(f"{out} is not a YAML list — cannot --append")

    seen_ids = {e.get("ps_id") for e in existing if isinstance(e, dict)}
    fresh = [e for e in entries if e.get("ps_id") not in seen_ids or e.get("ps_id") is None]
    merged = existing + [_ordered(e) for e in fresh]

    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Reconstructed pre-2012 travel — Polarsteps import (REVIEW BEFORE USE).\n")
        fh.write("# Prune/correct these, then merge into the data repo's backfill.yaml.\n")
        fh.write("# 'trip'/'ps_id' are provenance only; build_backfill.py ignores them.\n")
        yaml.safe_dump(merged, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=100)
    return len(fresh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Polarsteps export → reviewable backfill.yaml entries")
    ap.add_argument("--export", required=True,
                    help="path to the unzipped Polarsteps 'user_data' export dir")
    ap.add_argument("--out", default="backfill_polarsteps.yaml",
                    help="review file to write (default: backfill_polarsteps.yaml)")
    ap.add_argument("--flags",
                    default=str(Path(__file__).resolve().parent.parent
                                / "config" / "country_flags.json"),
                    help="path to country_flags.json (ISO2 mapping)")
    ap.add_argument("--before", type=int, default=2012,
                    help="keep steps strictly before this year (default 2012); "
                         "use --all to keep every step")
    ap.add_argument("--all", action="store_true",
                    help="ignore --before and import every step (WARNING: 2012+ "
                         "steps duplicate real Foursquare check-ins)")
    ap.add_argument("--append", action="store_true",
                    help="merge into an existing --out file (dedup by ps_id) "
                         "instead of overwriting")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    export = Path(args.export)
    if not (export / "trip").is_dir():
        log.error("no trip/ directory under %s — is this a Polarsteps export?", export)
        return 1

    iso2 = load_iso2_map(args.flags)
    before = None if args.all else args.before
    entries, total, skipped = build_entries(export, iso2, before)

    if not args.append and Path(args.out).exists():
        log.error("%s already exists — pass --append to merge, or choose a new "
                  "--out (refusing to overwrite)", args.out)
        return 1

    n_new = write_yaml(entries, args.out, args.append)

    window = "all years" if before is None else f"before {before}"
    log.info("Scanned %d step(s); kept %d deduped entr(y/ies) [%s]%s.",
             total, len(entries), window,
             f", {skipped} step(s) had no timestamp" if skipped else "")
    log.info("Wrote %d new entr(y/ies) → %s", n_new, args.out)
    log.info("REVIEW this file, prune/correct, then merge into backfill.yaml. "
             "Nothing reaches the dashboard until you do.")
    # Report countries touched, so the review has a quick sanity summary.
    by_country: dict[str, int] = {}
    for e in entries:
        by_country[e.get("country") or "?"] = by_country.get(e.get("country") or "?", 0) + 1
    for c, n in sorted(by_country.items(), key=lambda kv: (-kv[1], kv[0])):
        log.info("  %3d  %s", n, c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
