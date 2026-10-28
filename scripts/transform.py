# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
transform.py  –  Data cleaning and normalisation.

Loads mappings from config/ and applies them to raw CSV rows.
"""
from __future__ import annotations

import json
import math
import yaml
import logging
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ── Blank-city inference helpers ───────────────────────────────────────────────

def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in km between two (lat, lng) pairs."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _parse_detail(detail: str) -> list[tuple[str, str]]:
    """
    Parse the low_volume_checkins_detail field from the review CSV.
    Format: '2024-01-01T00:00:00Z::Venue Name || ...'
    Returns list of (iso_utc_str, venue_name).
    """
    if not detail:
        return []
    result = []
    for item in detail.split(" || "):
        parts = item.split("::", 1)
        if len(parts) == 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result


def build_blank_city_resolver(review_csv_path: str | Path):
    """
    Build a callable that infers a city for rows whose city field is blank.

    Two-pass strategy:
      1. Exact timestamp match — low-volume check-ins have their UTC timestamps
         stored verbatim in the review CSV, so we can match them precisely.
      2. Nearest centroid — high-volume check-ins are matched by finding the
         closest geographic centroid (country-filtered) within MAX_DIST_KM.

    Returns a function: resolve(row) -> str | None
      row is a dict with keys 'date', 'lat', 'lng', 'country'.
      Returns the inferred canonical city name, or None if unresolvable.
    """
    MAX_DIST_KM = 90  # covers ~99.6 % of blank rows; beyond this, locations are
                      # genuine remote/open-sea venues that should stay uncategorised

    review_csv_path = Path(review_csv_path)
    if not review_csv_path.exists():
        log.warning(
            "Blank-city review CSV not found at %s — inference disabled",
            review_csv_path,
        )
        return lambda row: None

    import csv as _csv
    with open(review_csv_path, encoding="utf-8-sig") as fh:
        review_rows = list(_csv.DictReader(fh))

    inferred_rows = [
        r for r in review_rows
        if r.get("decision_source") == "BLANK_CITY_INFERRED"
        and r.get("resolved_city", "").strip()
        and not r["resolved_city"].startswith("(BLANK")
    ]

    # Pass 1: unix-timestamp -> city  (from low_volume_checkins_detail)
    ts_map: dict[str, str] = {}
    for r in inferred_rows:
        city = r["resolved_city"]
        for ts_utc, _ in _parse_detail(r.get("low_volume_checkins_detail", "")):
            try:
                dt = datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                ts_map[str(int(dt.timestamp()))] = city
            except ValueError:
                pass

    # Pass 2: country -> list of (lat, lng, city) centroids for nearest-neighbour
    centroids: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for r in inferred_rows:
        try:
            centroids[r["country"]].append(
                (float(r["lat"]), float(r["lng"]), r["resolved_city"])
            )
        except (ValueError, TypeError):
            pass

    log.info(
        "Blank-city resolver ready: %d ts-mapped, %d centroids across %d countries",
        len(ts_map),
        sum(len(v) for v in centroids.values()),
        len(centroids),
    )

    def resolve(row: dict) -> str | None:
        # Pass 1: exact timestamp
        ts = row.get("date", "").strip()
        if ts and ts in ts_map:
            return ts_map[ts]

        # Pass 2: nearest centroid within MAX_DIST_KM
        try:
            lat = float(row["lat"])
            lng = float(row["lng"])
        except (ValueError, TypeError, KeyError):
            return None

        country = row.get("country", "")
        candidates = centroids.get(country, [])
        if not candidates:
            return None

        best_dist = float("inf")
        best_city = None
        for clat, clng, ccity in candidates:
            d = _haversine(lat, lng, clat, clng)
            if d < best_dist:
                best_dist = d
                best_city = ccity

        return best_city if best_dist <= MAX_DIST_KM else None

    return resolve


# ── Config loader ──────────────────────────────────────────────────────────────

#: Columns a companion_fixes.json entry may override, in the same order
#: metrics.collect_companions() reads them. sync_to_d1.py imports this so the
#: D1 reconcile patches exactly the same set.
COMPANION_FIX_FIELDS = ("with_name", "with_id", "overlaps_name", "overlaps_id")


def load_mappings(config_dir: str | Path = "config") -> dict[str, Any]:
    """Load city_merge (YAML), country_fixes and categories (JSON) from config/."""

    config_dir = Path(config_dir)

    def _load_file(name: str) -> dict:
        path = config_dir / name
        if not path.exists():
            print(f"Config file not found: {path} — skipping")
            return {}
        with open(path, encoding="utf-8") as fh:
            if path.suffix == ".yaml" or path.suffix == ".yml":
                return yaml.safe_load(fh) or {}
            elif path.suffix == ".json":
                return json.load(fh)
            else:
                print(f"Unknown config file type for {path}; must be .json or .yaml")
                return {}

    country_fixes = _load_file("country_fixes.json")
    city_fixes    = _load_file("city_fixes.json")   # per-timestamp city overrides
    venue_fixes   = _load_file("venue_fixes.json")  # per-venue_id city/country overrides
    companion_fixes = _load_file("companion_fixes.json")  # per-checkin_id companion overrides
    venue_category_fixes = _load_file("venue_category_fixes.json")  # per-venue_id category overrides
    city_merge    = _load_file("city_merge.yaml")
    cats_raw      = _load_file("categories.json")

    return {
        "country_fixes":   country_fixes,
        "city_fixes":      city_fixes,
        "venue_fixes":     venue_fixes,
        "companion_fixes": companion_fixes,
        "venue_category_fixes": {k: (v.get("category") if isinstance(v, dict) else v)
                                 for k, v in venue_category_fixes.items() if not k.startswith("_")},
        "city_merge":      city_merge,
        "category_groups": cats_raw.get("category_groups", {}),
        "explorer_groups": cats_raw.get("explorer_groups", {}),
    }


# ── Row-level transforms ───────────────────────────────────────────────────────

def apply_transforms(
    rows: list[dict],
    mappings: dict[str, Any],
    blank_city_resolver=None,
) -> list[dict]:
    """
    Apply country fixes, city merges, and (optionally) blank-city inference
    in-place. Returns the same list for convenience.

    Pass blank_city_resolver=build_blank_city_resolver(path) to fill in
    blank city fields using coordinate + timestamp inference.
    """
    country_fixes = mappings.get("country_fixes", {})
    city_fixes    = mappings.get("city_fixes", {})
    venue_fixes   = mappings.get("venue_fixes", {})
    companion_fixes = mappings.get("companion_fixes", {})
    venue_category_fixes = mappings.get("venue_category_fixes", {})
    city_merge    = mappings.get("city_merge", {})

    blank_filled  = 0
    malformed_dates = 0

    for row in rows:
        ts = row.get("date", "").strip()

        # Per-check-in companion override (companion_fixes.json), keyed by
        # checkin_id. Foursquare lets a friend be tagged onto a check-in AFTER
        # we snapshotted it, and nothing re-reads `with`/`shout` for an existing
        # row: the incremental fetch only appends new rows, enrich_overlaps reads
        # only `overlaps`, and the archive full re-fetch replaces each row with
        # the API version (so hand-editing checkins.csv is undone next month).
        # Fixing it here instead keeps the CSV a faithful API snapshot and makes
        # the correction survive every re-fetch. Applied FIRST — it is an
        # independent field set, and the venue_fixes branch below can `continue`.
        cfix = companion_fixes.get(row.get("checkin_id", "").strip())
        if isinstance(cfix, dict):
            for _f in COMPANION_FIX_FIELDS:
                if cfix.get(_f):
                    row[_f] = cfix[_f]

        # Per-venue category (venue_category_fixes.json): Foursquare's own
        # category is sometimes the wrong kind of place (Суперпрод, a corner
        # grocery, is a "Department Store" there). Pages still SHOW the
        # Foursquare category; only grouping (stats groups, category explorer,
        # month buckets) reads category_as via group_category(). Applied before
        # venue_fixes, whose branch below can `continue`.
        vcat = venue_category_fixes.get(row.get("venue_id", "").strip())
        if vcat:
            row["category_as"] = vcat

        # Per-venue override (venue_fixes.json) — HIGHEST priority. A single
        # venue_id entry pins city/country for every check-in at that venue,
        # past and future. Used for gateway venues (border crossings, airports)
        # whose Foursquare city/country is wrong or internally inconsistent.
        vfix = venue_fixes.get(row.get("venue_id", "").strip())
        if isinstance(vfix, dict):
            if vfix.get("country"):
                row["country"] = vfix["country"]
            if vfix.get("city"):
                row["city"] = vfix["city"]
                row["city_inferred"] = "0"
                continue

        # Country fix (keyed on unix timestamp string)
        if ts in country_fixes:
            row["country"] = country_fixes[ts]

        # Normalise alternate country name spellings (e.g. UN-approved "Türkiye"
        # returned by Foursquare for some check-ins → canonical "Turkey")
        if row.get("country") == "Türkiye":
            row["country"] = "Turkey"

        # Per-timestamp city override (city_fixes.json) — applied before
        # city_merge and blank-city inference so manual assignments win.
        if ts in city_fixes:
            row["city"] = city_fixes[ts]
            row["city_inferred"] = "0"
            continue

        # City normalisation
        # Unicode-normalise to NFC first: Foursquare sometimes returns diacritic
        # city names in NFD (decomposed, e.g. "Ha Noi" as a+U+0300, o+U+0323),
        # while city_merge keys are NFC (precomposed). Without this, NFD rows bypass
        # EVERY string-keyed rule (city_merge, city_fixes values, venue_fixes) and
        # resurface as phantom single-count cities (a duplicate "Soc Son" alongside
        # the correctly-merged Hanoi rows). Store the NFC value back so metrics/D1 agree.
        city = unicodedata.normalize("NFC", row.get("city", "").strip())
        row["city"] = city
        # Foursquare encodes apostrophes as U+2019 (RIGHT SINGLE QUOTATION MARK)
        # but city_merge keys use U+0027 (ASCII apostrophe).  Normalise first so
        # entries like "Kazan'" (167 rows), "Smarhon'" (7), "Nevel'" (8), etc. match.
        # Also normalise U+2018 (LEFT SINGLE QUOTATION MARK) used as Arabic ʿayn in
        # transliterations like "Al Ma'ādī", "Ma'ādī al Khabīrī", etc.
        city_normalised = city.replace("\u2019", "'").replace("\u2018", "'")

        if not city and blank_city_resolver is not None:
            # Infer city for blank-city rows from coordinates / timestamp
            inferred = blank_city_resolver(row)
            if inferred:
                inferred = unicodedata.normalize("NFC", inferred)
                # Also run city_merge on the inferred name — the review CSV may
                # store raw Foursquare values (Cyrillic, RSQM apostrophes, variants)
                # that still need normalising, e.g. "Мачулищи" → "Machulishchy".
                inferred_norm = inferred.replace("\u2019", "'").replace("\u2018", "'")
                row["city"] = city_merge.get(inferred_norm,
                              city_merge.get(inferred, inferred))
                blank_filled += 1
                row["city_inferred"] = "1"
        elif city_normalised in city_merge:
            row["city"] = city_merge[city_normalised]
        elif city in city_merge:
            row["city"] = city_merge[city]

        row.setdefault("city_inferred", "0")

        # Validate date
        if ts and not ts.lstrip("-").isdigit():
            malformed_dates += 1

    if blank_filled:
        log.info("Blank-city inference filled %d rows", blank_filled)
    if malformed_dates:
        log.warning("%d rows had non-numeric 'date' values", malformed_dates)

    return rows


# ── Category helpers ───────────────────────────────────────────────────────────

OTHER_GROUP = "Other"


def group_category(row: dict) -> str:
    """The category a check-in is GROUPED by: the venue_category_fixes pin when
    there is one, else Foursquare's own. Display always uses row["category"]."""
    return (row.get("category_as") or row.get("category") or "").strip()


RENTAL_CATEGORIES = frozenset({"Apartment or Condo"})
RENTAL_AS = "Vacation Rental"


def home_countries(rows: list[dict], home_cities: set[str]) -> set[str]:
    """The country each home city sits in, by its most common country among the
    rows (a border-gateway row tagged with the neighbour must not count)."""
    seen: dict[str, Counter] = {}
    for r in rows:
        city = (r.get("city") or "").strip()
        if city in home_cities and r.get("country"):
            seen.setdefault(city, Counter())[r["country"]] += 1
    return {c.most_common(1)[0][0] for c in seen.values()}


RENTAL_GAP_DAYS = 14   # a longer break between check-ins means a second, separate visit


def _is_one_stay(stamps: list[int]) -> bool:
    """Two or more days, all in one visit: the shape of a rented flat. A single
    day is a call on a friend or a famous building; coming back on another
    trip (Культуры 10 over eight years) is a friend's home."""
    stamps = sorted(stamps)
    days = {t // 86400 for t in stamps}
    return len(days) >= 2 and all(b - a <= RENTAL_GAP_DAYS * 86400 for a, b in pairwise(stamps))


def pin_rentals(rows: list[dict], home_country_set: set[str]) -> int:
    """Group a rented "Apartment or Condo" as lodging.

    Foursquare files every flat under Home & Office, so a rented apartment in
    Lisbon counted as home. A flat counts as a rental only when all three hold:
      • it is abroad — a country that was never home (a flat in Chișinău during
        the Minsk years, or in Hrodna, is family's or a friend's);
      • its check-ins span two or more days;
      • they all belong to one visit (see _is_one_stay) — friends get revisited.
    A venue_category_fixes pin (category_as) wins over the rule either way.
    Returns the number of rows changed."""
    cand: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("category_as") or (r.get("category") or "").strip() not in RENTAL_CATEGORIES:
            continue
        if (r.get("country") or "").strip() in home_country_set:
            continue
        cand[(r.get("venue_id") or "").strip() or r.get("venue", "")].append(r)
    n = 0
    for vrows in cand.values():
        try:
            stamps = [int(r.get("date") or 0) for r in vrows]
        except ValueError:
            continue
        if not _is_one_stay(stamps):
            continue
        for r in vrows:
            r["category_as"] = RENTAL_AS
        n += len(vrows)
    return n


def build_categorize_fn(category_groups: dict[str, list[str]]):
    """Return a function that maps a raw category string to its group.

    Exact names only (case-insensitive). The old substring fallback put "Event
    Space" in Sport ("spa"), "Port" in Food & Drink ("sports bar") and "Public
    Transportation" in Food & Drink ("pub"). categories.json lists every raw
    category the data has; one it does not know yet is OTHER_GROUP (and
    metrics.stats logs it), and an empty category is None."""
    fast: dict[str, str] = {}
    for group, names in category_groups.items():
        if group.startswith("_"):
            continue
        for name in names:
            fast[name.lower()] = group

    def categorize(raw: str) -> str | None:
        cl = (raw or "").strip().lower()
        if not cl:
            return None
        return fast.get(cl, OTHER_GROUP)

    return categorize
