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

    Returns a function: resolve(row, live=None) -> str | None
      row is a dict with keys 'date', 'lat', 'lng', 'country'.
      live: live_city_points(rows) - where your real check-ins put each city. They
      join the review CSV's points, so a city the review never saw (Liege,
      Eindhoven) can be the nearest one instead of whatever reviewed city lies
      within reach (Charleroi, Rotterdam).
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
        return lambda row, live=None: None

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

    reviewed: dict[str, set[str]] = defaultdict(set)
    for _c, _pts in centroids.items():
        for _a, _b, _city in _pts:
            reviewed[_c].add(unicodedata.normalize("NFC", _city))
    # a reviewed raw name may be merged later (Мачулищи -> Machulishchy); the
    # review CSV sits in the config dir next to city_merge.yaml
    try:
        reviewed_merge = load_mappings(review_csv_path.parent).get("city_merge", {})
    except Exception:  # noqa: BLE001 — a missing/odd config only weakens the filter
        reviewed_merge = {}
    for _c in list(reviewed):
        reviewed[_c] |= {reviewed_merge.get(x, x) for x in reviewed[_c]}

    log.info(
        "Blank-city resolver ready: %d ts-mapped, %d centroids across %d countries",
        len(ts_map),
        sum(len(v) for v in centroids.values()),
        len(centroids),
    )

    def resolve(row: dict, live: dict | None = None) -> str | None:
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
        best_dist = float("inf")
        best_city = None
        for clat, clng, ccity in centroids.get(country, []):
            d = _haversine(lat, lng, clat, clng)
            if d < best_dist:
                best_dist = d
                best_city = ccity

        # A city the review never saw (Liege, Eindhoven, Amsterdam) wins only where
        # the review has nothing close and a real check-in of that city is a few km
        # away; elsewhere the reviewed points decide as before (around Minsk the
        # review knows every suburb Foursquare would call "Minsk").
        if best_dist > LIVE_REVIEW_FAR_KM:
            known = reviewed.get(country, set())
            near = [(_haversine(lat, lng, a, b), c) for a, b, c in live_near(live, country, lat, lng)
                    if c not in known]
            if near:
                d, c = min(near)
                if d <= LIVE_NEAR_KM:
                    return c

        return best_city if best_dist <= MAX_DIST_KM else None

    return resolve


# ── Config loader ──────────────────────────────────────────────────────────────

#: Columns a companion_fixes.json entry may override, in the same order
#: metrics.collect_companions() reads them. sync_to_d1.py imports this so the
#: D1 reconcile patches exactly the same set.
COMPANION_FIX_FIELDS = ("with_name", "with_id", "overlaps_name", "overlaps_id")
#: companion_fixes.json key that REMOVES people (comma-separated user ids) from a
#: check-in's with_* and overlaps_* lists — a friend tagged by mistake that Swarm
#: will not let you untag. Applied after the overrides above.
COMPANION_REMOVE_FIELD = "remove_ids"
#: companion_fixes.json key that REPLACES the check-in's shout text. Unlike the
#: fields above it is applied whenever present, even empty: "" hides a shout
#: (e.g. the "with X" text of a mistaken tag).
COMPANION_SHOUT_FIELD = "shout"


def remove_companions(row: dict, ids: str) -> None:
    """Drop the users in `ids` from row's with_name/with_id and overlaps_name/
    overlaps_id, keeping the name↔id pairs aligned. A list whose names and ids do
    not line up is left alone (removing by position would hit the wrong person).
    An emptied overlaps_id keeps the "-" sentinel ("checked, nobody")."""
    drop = {i.strip() for i in (ids or "").split(",") if i.strip()}
    if not drop:
        return
    for name_f, id_f in (("with_name", "with_id"), ("overlaps_name", "overlaps_id")):
        id_list = [i.strip() for i in (row.get(id_f) or "").split(",") if i.strip() and i.strip() != "-"]
        if not drop.intersection(id_list):
            continue
        names = [n.strip() for n in (row.get(name_f) or "").replace(" ,", ",").split(",") if n.strip()]
        if len(names) != len(id_list):
            continue
        kept = [(n, i) for n, i in zip(names, id_list) if i not in drop]
        row[name_f] = ", ".join(n for n, _ in kept)
        row[id_f] = ", ".join(i for _, i in kept) or ("-" if id_f == "overlaps_id" else "")


#: Row key set by apply_transforms on a check-in whose companions an alias rewrote,
#: so sync_to_d1.py can reconcile exactly those rows in D1.
COMPANION_ALIASED_KEY = "_companion_aliased"


def merge_companion_aliases(row: dict, aliases: dict) -> bool:
    """Rewrite secondary Swarm accounts to one person (companion_aliases.json:
    {alias user id: {"id": canonical id, "name": display name}}) in with_*,
    created_by_* and overlaps_*. A list whose names and ids do not line up is left
    alone; a person listed twice after the rewrite is kept once. True if changed."""
    if not aliases:
        return False
    changed = False
    for name_f, id_f in (("with_name", "with_id"), ("overlaps_name", "overlaps_id"),
                         ("created_by_name", "created_by_id")):
        id_list = [i.strip() for i in (row.get(id_f) or "").split(",") if i.strip() and i.strip() != "-"]
        if not any(i in aliases for i in id_list):
            continue
        names = [n.strip() for n in (row.get(name_f) or "").replace(" ,", ",").split(",") if n.strip()]
        if len(names) != len(id_list):
            continue
        out: list[tuple[str, str]] = []
        for n, i in zip(names, id_list):
            a = aliases.get(i)
            if isinstance(a, dict) and a.get("id"):
                n, i = a.get("name") or n, str(a["id"])
            if i not in {x for _, x in out}:
                out.append((n, i))
        row[name_f] = ", ".join(n for n, _ in out)
        row[id_f] = ", ".join(i for _, i in out)
        changed = True
    return changed


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
    companion_aliases = {k: v for k, v in _load_file("companion_aliases.json").items()
                         if not k.startswith("_")}  # second Swarm accounts -> one person
    venue_category_fixes = _load_file("venue_category_fixes.json")  # per-venue_id category overrides
    city_merge    = _load_file("city_merge.yaml")
    cats_raw      = _load_file("categories.json")

    return {
        "country_fixes":   country_fixes,
        "city_fixes":      city_fixes,
        "venue_fixes":     venue_fixes,
        # venue ids that never make a day in a place (stats "By Days"): border shops, transfer desks
        # venues BETWEEN two countries: for the index "By Days" only (straddle_sides)
        "straddle_venues": {k: v for k, v in _load_file("straddle_venues.json").items() if not k.startswith("_")},
        "day_skip_venues": {k for k in _load_file("day_skip_venues.json") if not k.startswith("_")},
        "companion_fixes": companion_fixes,
        "companion_aliases": companion_aliases,
        "venue_category_fixes": {k: (v.get("category") if isinstance(v, dict) else v)
                                 for k, v in venue_category_fixes.items() if not k.startswith("_")},
        "city_merge":      city_merge,
        "category_groups": cats_raw.get("category_groups", {}),
        "explorer_groups": cats_raw.get("explorer_groups", {}),
    }


# ── Row-level transforms ───────────────────────────────────────────────────────

#: Raw city names that are a PART of some city, not a place of their own:
#: "Downtown" is Cairo's centre in city_merge, but Foursquare labels the centre of
#: Alexandria the same way. Such a merge only holds near the city it names.
GENERIC_CITY_NAMES = frozenset({
    "downtown", "centre", "center", "centro", "city centre", "city center", "old town",
    "old city", "medina", "wust el-balad", "centrum", "stare miasto", "altstadt", "midtown",
})
GENERIC_MERGE_KM = 50
LIVE_CITY_MIN = 2
LIVE_NEAR_KM = 3.0         # a real check-in this close names the place
LIVE_REVIEW_FAR_KM = 15.0  # ...when no reviewed point is within this


LivePoints = dict[str, dict[tuple[int, int], list[tuple[float, float, str]]]]


def live_city_points(rows: list[dict], skip: set[int] | frozenset[int] = frozenset()) -> LivePoints:
    """Where your real check-ins say each city is: {country: {1-degree bucket:
    [(lat, lng, city)]}}, one point per ~1 km cell (its most common city). A blank
    row then takes the city of the NEAREST real check-in, not of the nearest city
    centre, which inside a big city (Minsk) is often a suburb's. Cities named by
    fewer than LIVE_CITY_MIN check-ins, inferred rows and whole-country check-ins
    do not count."""
    cells: dict[tuple[str, int, int], Counter] = defaultdict(Counter)
    per_city: Counter = Counter()
    for i, r in enumerate(rows):
        if i in skip or not r.get("city") or r.get("city_inferred") == "1":
            continue
        if (r.get("category") or "").strip() in _COUNTRY_LEVEL:
            continue
        try:
            la, ln = float(r["lat"]), float(r["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        cells[(r.get("country", ""), round(la * 100), round(ln * 100))][r["city"]] += 1
        per_city[(r["city"], r.get("country", ""))] += 1
    out: LivePoints = defaultdict(lambda: defaultdict(list))
    for (country, la100, ln100), cnt in cells.items():
        city = next((c for c, _ in cnt.most_common() if per_city[(c, country)] >= LIVE_CITY_MIN), None)
        if city:
            la, ln = la100 / 100, ln100 / 100
            out[country][(math.floor(la), math.floor(ln))].append((la, ln, city))
    return out


def live_near(live: LivePoints | None, country: str, lat: float, lng: float) -> list[tuple[float, float, str]]:
    """The live points of `country` in the 3x3 one-degree buckets around a spot."""
    b = (live or {}).get(country)
    if not b:
        return []
    la, ln = math.floor(lat), math.floor(lng)
    return [p for dl in (-1, 0, 1) for dn in (-1, 0, 1) for p in b.get((la + dl, ln + dn), [])]


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
    companion_aliases = mappings.get("companion_aliases", {})
    venue_category_fixes = mappings.get("venue_category_fixes", {})
    city_merge    = mappings.get("city_merge", {})

    blank_filled  = 0
    malformed_dates = 0
    pending: list[int] = []          # blank cities, inferred once every real city is known
    generic: list[int] = []          # merged from a generic name ("Downtown"), checked by distance

    for _idx, row in enumerate(rows):
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
            if cfix.get(COMPANION_REMOVE_FIELD):
                remove_companions(row, cfix[COMPANION_REMOVE_FIELD])
            if isinstance(cfix.get(COMPANION_SHOUT_FIELD), str):
                row["shout"] = cfix[COMPANION_SHOUT_FIELD]
        # One person, two Swarm accounts (companion_aliases.json) — after the
        # per-check-in fixes, so a fix may name either account.
        if merge_companion_aliases(row, companion_aliases):
            row[COMPANION_ALIASED_KEY] = "1"

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
            pending.append(_idx)     # inferred below, against every real city
        elif city_normalised in city_merge:
            row["city"] = city_merge[city_normalised]
            if city_normalised.lower() in GENERIC_CITY_NAMES:
                generic.append(_idx)
        elif city in city_merge:
            row["city"] = city_merge[city]
            if city.lower() in GENERIC_CITY_NAMES:
                generic.append(_idx)

        row.setdefault("city_inferred", "0")

        # Validate date
        if ts and not ts.lstrip("-").isdigit():
            malformed_dates += 1

    if blank_city_resolver is not None and (pending or generic):
        live = live_city_points(rows, skip=set(generic))
        for i in generic:
            # "Downtown" -> Cairo holds in Cairo; downtown Alexandria is inferred instead.
            r = rows[i]
            try:
                la, ln = float(r["lat"]), float(r["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            near = [_haversine(la, ln, a, b) for a, b, c in live_near(live, r.get("country", ""), la, ln)
                    if c == r["city"]]
            if not near or min(near) > GENERIC_MERGE_KM:
                r["city"] = ""
                pending.append(i)
        for i in pending:
            row = rows[i]
            inferred = blank_city_resolver(row, live)
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

    # A whole-country check-in ("Russia" from a train, "Hungary" at the line)
    # inherits the city you were in a moment ago — often across the border, so
    # "Minsk" turned up in Russia and "Vienna" in Hungary. Such a row keeps its
    # country and loses a city that belongs to another country.
    foreign = drop_foreign_city_on_country_rows(rows)
    if foreign:
        log.info("Whole-country check-ins: %d foreign city name(s) dropped", foreign)

    if blank_filled:
        log.info("Blank-city inference filled %d rows", blank_filled)
    if malformed_dates:
        log.warning("%d rows had non-numeric 'date' values", malformed_dates)

    return rows


STRADDLE_WINDOW_S = 12 * 3600


_COUNTRY_LEVEL = frozenset({"Country", "State"})


def drop_foreign_city_on_country_rows(rows: list[dict]) -> int:
    """Blank the city of a whole-country check-in when that city is another country's.

    The city is kept when some real (non-country) check-in places it in this same
    country — a border twin like Sculeni exists on both sides. Returns the count."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r.get("city") and (r.get("category") or "").strip() not in _COUNTRY_LEVEL:
            votes[r["city"]][r.get("country", "")] += 1
    n = 0
    for r in rows:
        city = r.get("city")
        if not city or (r.get("category") or "").strip() not in _COUNTRY_LEVEL:
            continue
        v = votes.get(city)
        if v and not v.get(r.get("country", "")):
            r["city"] = ""
            n += 1
    return n


def straddle_sides(rows: list[dict], straddles: dict[str, dict]) -> dict[int, tuple[str, str]]:
    """For the index "By Days" only — rows are NOT changed (the feed, D1 and every
    other stat keep the venue's own side). A venue BETWEEN two countries
    (config/straddle_venues.json) counts, per check-in, for the side you were on
    before it: the country of the previous check-in within STRADDLE_WINDOW_S (other
    straddle venues skipped). With no earlier one, the side opposite the next
    check-in (you were crossing towards it). → {row index: (country, city)} for the
    check-ins whose side differs from the row's own."""
    if not straddles:
        return {}

    def ts(r: dict) -> int:
        try:
            return int(r.get("date") or 0)
        except (TypeError, ValueError):
            return 0
    order = sorted(range(len(rows)), key=lambda i: ts(rows[i]))
    is_s = [(rows[i].get("venue_id") or "").strip() in straddles for i in order]
    out: dict[int, tuple[str, str]] = {}
    for k, i in enumerate(order):
        if not is_s[k]:
            continue
        r = rows[i]
        sides = {c: city for c, city in straddles[(r.get("venue_id") or "").strip()].items()
                 if not c.startswith("_")}
        t = ts(r)
        side = None
        for j in range(k - 1, -1, -1):
            p = rows[order[j]]
            if t - ts(p) > STRADDLE_WINDOW_S:
                break
            if not is_s[j] and p.get("country"):
                side = p["country"] if p["country"] in sides else None
                break
        if side is None:
            for j in range(k + 1, len(order)):
                n = rows[order[j]]
                if ts(n) - t > STRADDLE_WINDOW_S:
                    break
                if not is_s[j] and n.get("country"):
                    other = [c for c in sides if c != n["country"]]
                    side = other[0] if n["country"] in sides and len(other) == 1 else None
                    break
        if side and (r.get("country"), r.get("city")) != (side, sides[side]):
            out[i] = (side, sides[side])
    return out


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
