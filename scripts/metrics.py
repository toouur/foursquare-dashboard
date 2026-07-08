# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
metrics.py  –  All aggregation and trip-detection logic.

Depends on: transform.py
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from transform import build_categorize_fn

log = logging.getLogger(__name__)


# ── Date helpers ───────────────────────────────────────────────────────────────

def _parse_ts(row: dict) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(row["date"]), tz=timezone.utc)
    except (ValueError, KeyError, TypeError, OSError):
        return None


# Try to use timezonefinder for accurate lat/lng → IANA timezone resolution.
# Fall back to a longitude-based approximation (±30 min) if not installed.
try:
    from timezonefinder import TimezoneFinder as _TZF
    _tf = _TZF()

    def _tz_at(lat: float | None, lng: float | None) -> str:
        """Return the IANA timezone name for a coordinate pair."""
        if lat is None or lng is None:
            return "UTC"
        tz = _tf.timezone_at(lat=lat, lng=lng)
        return tz if tz else "UTC"

except ImportError:
    log.warning(
        "timezonefinder not installed — falling back to longitude-based timezone "
        "approximation (±30 min accuracy). Run: pip install timezonefinder"
    )

    def _tz_at(lat: float | None, lng: float | None) -> str:  # type: ignore[misc]
        if lng is None:
            return "UTC"
        offset = max(-12, min(14, round(lng / 15)))
        if offset == 0:
            return "Etc/GMT"
        sign = "-" if offset > 0 else "+"
        return f"Etc/GMT{sign}{abs(offset)}"


# Country → IANA timezone for countries with a single timezone (no DST surprises).
# Belarus stays at UTC+3 year-round (Europe/Minsk); longitude-based fallback gives UTC+2.
_COUNTRY_TZ: dict[str, str] = {
    'Belarus': 'Europe/Minsk', 'Moldova': 'Europe/Chisinau', 'Poland': 'Europe/Warsaw',
    'Ukraine': 'Europe/Kyiv', 'Italy': 'Europe/Rome', 'Romania': 'Europe/Bucharest',
    'Lithuania': 'Europe/Vilnius', 'Germany': 'Europe/Berlin',
    'Türkiye': 'Europe/Istanbul', 'Turkey': 'Europe/Istanbul',
    'China': 'Asia/Shanghai', 'Spain': 'Europe/Madrid', 'Georgia': 'Asia/Tbilisi',
    'France': 'Europe/Paris', 'India': 'Asia/Kolkata', 'Latvia': 'Europe/Riga',
    'Portugal': 'Europe/Lisbon', 'Iran': 'Asia/Tehran', 'Egypt': 'Africa/Cairo',
    'Japan': 'Asia/Tokyo', 'United Kingdom': 'Europe/London',
    'Czechia': 'Europe/Prague', 'Czech Republic': 'Europe/Prague',
    'Hungary': 'Europe/Budapest', 'Austria': 'Europe/Vienna',
    'Switzerland': 'Europe/Zurich', 'Netherlands': 'Europe/Amsterdam',
    'Belgium': 'Europe/Brussels', 'Slovakia': 'Europe/Bratislava',
    'Bulgaria': 'Europe/Sofia', 'Greece': 'Europe/Athens', 'Croatia': 'Europe/Zagreb',
    'Serbia': 'Europe/Belgrade', 'Estonia': 'Europe/Tallinn',
    'Finland': 'Europe/Helsinki', 'Sweden': 'Europe/Stockholm',
    'Norway': 'Europe/Oslo', 'Denmark': 'Europe/Copenhagen',
    'Kazakhstan': 'Asia/Almaty', 'Uzbekistan': 'Asia/Tashkent',
    'Azerbaijan': 'Asia/Baku', 'Armenia': 'Asia/Yerevan',
    'Israel': 'Asia/Jerusalem', 'Jordan': 'Asia/Amman',
    'Thailand': 'Asia/Bangkok', 'Vietnam': 'Asia/Ho_Chi_Minh',
    'Indonesia': 'Asia/Jakarta', 'South Korea': 'Asia/Seoul', 'Taiwan': 'Asia/Taipei',
    'Singapore': 'Asia/Singapore', 'Malaysia': 'Asia/Kuala_Lumpur',
    'Pakistan': 'Asia/Karachi', 'Nepal': 'Asia/Kathmandu',
    'Mongolia': 'Asia/Ulaanbaatar', 'Morocco': 'Africa/Casablanca',
    'Tunisia': 'Africa/Tunis', 'South Africa': 'Africa/Johannesburg',
    'New Zealand': 'Pacific/Auckland', 'Holy See (Vatican City State)': 'Europe/Rome',
    'San Marino': 'Europe/Rome', 'Monaco': 'Europe/Monaco', 'Malta': 'Europe/Malta',
    'Cyprus': 'Asia/Nicosia', 'Iceland': 'Atlantic/Reykjavik', 'Ireland': 'Europe/Dublin',
    'Slovenia': 'Europe/Ljubljana', 'North Macedonia': 'Europe/Skopje',
    'Albania': 'Europe/Tirane', 'Montenegro': 'Europe/Podgorica',
    'Bosnia and Herzegovina': 'Europe/Sarajevo', 'Kosovo': 'Europe/Belgrade',
    'Tajikistan': 'Asia/Dushanbe', 'Kyrgyzstan': 'Asia/Bishkek',
    'Turkmenistan': 'Asia/Ashgabat', 'Oman': 'Asia/Muscat',
    'Saudi Arabia': 'Asia/Riyadh', 'United Arab Emirates': 'Asia/Dubai',
    'Qatar': 'Asia/Qatar', 'Kuwait': 'Asia/Kuwait', 'Bahrain': 'Asia/Bahrain',
    'Iraq': 'Asia/Baghdad', 'Lebanon': 'Asia/Beirut', 'Myanmar': 'Asia/Rangoon',
    'Cambodia': 'Asia/Phnom_Penh', 'Philippines': 'Asia/Manila',
    'Bangladesh': 'Asia/Dhaka', 'Sri Lanka': 'Asia/Colombo',
    'Chile': 'America/Santiago', 'Argentina': 'America/Argentina/Buenos_Aires',
    'Uruguay': 'America/Montevideo', 'Bolivia': 'America/La_Paz',
    'Peru': 'America/Lima', 'Ecuador': 'America/Guayaquil',
    'Colombia': 'America/Bogota', 'Venezuela': 'America/Caracas',
    'Panama': 'America/Panama', 'Costa Rica': 'America/Costa_Rica',
    'Cuba': 'America/Havana', 'Jamaica': 'America/Jamaica',
}


def _localise(d: datetime, lat: float | None, lng: float | None, country: str = '') -> datetime:
    """Shift a UTC datetime to local time at the given coordinates.

    Uses country name first (accurate for countries without DST surprises, e.g. Belarus),
    then falls back to coordinate-based timezone resolution.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if country and country in _COUNTRY_TZ:
        try:
            return d.astimezone(ZoneInfo(_COUNTRY_TZ[country]))
        except Exception:
            pass
    try:
        return d.astimezone(ZoneInfo(_tz_at(lat, lng)))
    except (ZoneInfoNotFoundError, Exception):
        return d


# ── Trip detection ─────────────────────────────────────────────────────────────

# Home-city venue categories that mark the start/end of a journey.
# A check-in at one of these immediately before or after a trip is included
# as the departure / arrival leg even though it's in the home city.
_TRANSPORT_CATEGORIES: frozenset[str] = frozenset({
    "Rail Station", "Train Station", "Airport", "Light Rail Station",
    "Bus Station", "Bus Terminal", "Ferry Terminal",
})

# Categories that, when a bicycle trip is in progress, should NOT act as a
# home signal.  On departure-day, blank-city check-ins at bike paths, roads,
# parks etc. GPS-resolve to the home city — but they belong to the ride, not
# to "being at home".  Only applied to trips tagged "bicycle".
_BICYCLE_PASSTHROUGH_CATS: frozenset[str] = frozenset({
    "Sports and Recreation", "Road", "Bridge", "River", "Lake",
    "Waterfall", "Park", "Trail", "Bike Trail", "Other Great Outdoors",
    "Beach", "Reservoir", "Bike Rental",
})

def _is_home_transport(row: dict, home_city: str) -> bool:
    return (
        row.get("city", "").strip() == home_city
        and row.get("category", "").strip() in _TRANSPORT_CATEGORIES
    )


def detect_trips(
    rows: list[dict],
    home_city: str = "Minsk",
    min_checkins: int = 5,
    trip_names: dict[str, str] | None = None,
    trip_exclude: set[int] | None = None,
    trip_end_overrides: dict[int, int] | None = None,
    trip_start_overrides: dict[int, int] | None = None,
    trip_tags: dict[int, list[str]] | None = None,
) -> list[dict]:
    """
    Detect trips as consecutive non-home sequences of check-ins.

    trip_names: optional dict mapping str(start_ts) → custom trip name.
    Each trip is extended by one check-in on each side if the immediately
    adjacent home-city check-in is at a transport hub (station / airport).

    Returns a list of trip dicts sorted chronologically, each containing:
    id, name, start_date, end_date, duration, countries, cities,
    checkin_count, unique_places, checkins, coords, unique_pts, top_cats.
    """
    valid = sorted(
        [r for r in rows if r.get("date", "").strip()],
        key=lambda r: int(r["date"]),
    )

    raw_trips: list[list[dict]] = []
    current: list[dict] = []
    for row in valid:
        if row.get("city", "").strip() != home_city:
            current.append(row)
        else:
            if current:
                raw_trips.append(current)
            current = []
    if current:
        raw_trips.append(current)

    # Apply trip_end_overrides as splits when the override timestamp is before the
    # trip's natural end.  A raw trip whose start_ts matches an override key and
    # whose end extends past force_end_ts is cut in two: rows up to force_end_ts
    # become the first trip; the remainder becomes a new raw trip that will be
    # extended and named independently.  When force_end_ts >= current end the
    # override is handled later (extension phase) as before.
    if trip_end_overrides:
        split_raw: list[list[dict]] = []
        for trip in raw_trips:
            trip_ts_start = int(trip[0]["date"])
            trip_ts_end   = int(trip[-1]["date"])
            # Match override key by range: the key may be inside the raw trip
            # (not necessarily the very first row) when departure rows are blank-city.
            force_end_ts = next(
                (v for k, v in trip_end_overrides.items()
                 if trip_ts_start <= k <= trip_ts_end),
                None,
            )
            if force_end_ts is not None and force_end_ts < trip_ts_end:
                part1 = [r for r in trip if int(r["date"]) <= force_end_ts]
                part2 = [r for r in trip if int(r["date"]) > force_end_ts]
                if part1:
                    split_raw.append(part1)
                if part2:
                    split_raw.append(part2)
            else:
                split_raw.append(trip)
        raw_trips = split_raw

    # Extend trip boundaries to include departure / arrival legs in home city.
    #
    # Both scans traverse home-city rows AND blank-city rows (highways, country
    # markers, fuel stops with unresolved city).  Scanning stops when a row with
    # a *different* non-blank city is encountered, or the 24 h window is exceeded.
    # This handles common patterns like:
    #   Departure: Bus Station (Minsk) → Bus Line (Minsk) → M6 road (blank) → border
    #   Arrival:   border → country marker (blank) → M1 highway (blank) → Minsk City
    #              → Bus Station (Minsk)
    #
    # Departure (backward scan):
    #   Track the EARLIEST transport hub found so a bus-station→airport chain
    #   picks up the bus station.  Include ALL rows from that hub to trip start.
    #
    # Arrival (forward scan):
    #   Find the NEAREST (first) home-city transport hub within 24 h and include
    #   all rows from trip end up to that hub.  Using nearest avoids pulling in
    #   home activity that precedes a later, unrelated departure.
    #
    # min_checkins is applied to the raw (non-home-only) trip before extension so
    # that hub / intermediate rows cannot manufacture trips from trivial outings.
    _24H = 86_400
    pos = {id(r): i for i, r in enumerate(valid)}

    # Pre-filter: drop trips that are too short even before hub extension.
    # Allow trips that are one check-in short of the threshold — a single hub
    # extension may legitimately push them over (e.g. 4 non-home + 1 hub = 5).
    raw_trips = [t for t in raw_trips if len(t) >= max(1, min_checkins - 1)]

    extended: list[tuple] = []   # (ext_rows, resolved_tags, name_ts)
    for trip_rows in raw_trips:
        ext = list(trip_rows)
        fp = pos[id(trip_rows[0])]
        lp = pos[id(trip_rows[-1])]

        # --- Departure ---
        # Scan backward through home-city AND blank-city rows within 24 h.
        # Stop at any non-blank non-home-city row (reached a different city →
        # either previous trip or unrelated check-in).
        # Rules for hub chaining:
        #   • Different venue (e.g. Bus Station → Airport): extend further back
        #     so the chain starts at the earlier/outer hub.
        #   • Same venue ID repeated (e.g. Railway Station visited twice — once
        #     for ticket buying, once for actual departure): keep only the LATER
        #     occurrence (nearest to the trip start).  Going back to the earlier
        #     duplicate would incorrectly include inter-visit home activity.
        trip_start_ts = int(trip_rows[0]["date"])
        # Index of the last row included in the previous extended trip.
        # A transport hub at or before this index was already consumed as an
        # arrival leg — don't reuse it as a departure hub for this trip.
        prev_end_idx = pos[id(extended[-1][0][-1])] if extended else -1
        dep_hub: int | None = None
        i = fp - 1
        while i >= 0:
            if i <= prev_end_idx:
                break  # reached rows already owned by the previous trip
            row_city = valid[i].get("city", "").strip()
            if trip_start_ts - int(valid[i]["date"]) > _24H:
                break
            if row_city == home_city:
                if _is_home_transport(valid[i], home_city):
                    if dep_hub is None:
                        dep_hub = i  # first hub found (nearest to trip start)
                    else:
                        cur_vid  = valid[dep_hub].get("venue_id", "").strip()
                        new_vid  = valid[i].get("venue_id", "").strip()
                        if new_vid and cur_vid and new_vid == cur_vid:
                            pass  # same venue repeated — keep the later one
                        else:
                            # Only chain to an earlier hub if it's within 3 h of
                            # the current dep_hub.  A larger gap means the earlier
                            # Rail Station visit was an unrelated activity (e.g. a
                            # local bike-tour check-in the day before departure).
                            _MAX_HUB_CHAIN_GAP = 3 * 3600
                            hub_gap = int(valid[dep_hub]["date"]) - int(valid[i]["date"])
                            if hub_gap <= _MAX_HUB_CHAIN_GAP:
                                dep_hub = i  # different venue → extend chain earlier
            elif row_city != "":
                break  # different non-blank city → stop (avoid previous trips)
            i -= 1
        if dep_hub is not None:
            # Include hub + all rows between hub and trip start
            ext = valid[dep_hub:fp] + ext

        # --- Departure same-day extension (car/marshrutka — no transport hub found) ---
        # If no transport hub was found, scan backward through home-city and
        # blank-city rows on the SAME UTC date as the first trip check-in for:
        #   • "Transportation Service" or "Bus Line" (marshrutka, taxi app, etc.) —
        #     preferred; keep scanning to find the earliest occurrence (the departure
        #     vehicle)
        #   • "Fuel Station" — fallback; take the nearest one (closest to departure)
        # Blank-city rows are included because marshrutka check-ins are often made
        # en route with no city assigned yet.
        if dep_hub is None:
            trip_start_utc_date = datetime.fromtimestamp(trip_start_ts, tz=timezone.utc).date()
            fuel_dep: int | None = None          # nearest Fuel Station
            transport_svc_dep: int | None = None  # earliest Transportation Service / Bus Line / Parking
            i = fp - 1
            while i >= 0:
                if i <= prev_end_idx:
                    break  # don't scan into rows already owned by previous trip's arrival
                row_ts = int(valid[i]["date"])
                if trip_start_ts - row_ts > _24H:
                    break
                row_city = valid[i].get("city", "").strip()
                if row_city == home_city or row_city == "":
                    cat = valid[i].get("category", "").strip()
                    row_date = datetime.fromtimestamp(row_ts, tz=timezone.utc).date()
                    if row_date == trip_start_utc_date:
                        if cat in ("Transportation Service", "Bus Line", "Parking"):
                            transport_svc_dep = i  # keep going — want earliest
                        elif cat == "Fuel Station" and fuel_dep is None:
                            fuel_dep = i  # nearest fuel station
                elif row_city != "":
                    break
                i -= 1
            # Transportation Service wins over Fuel Station when both present.
            # Gap filter: if chosen departure is more than 4 h before the first
            # trip check-in, it's a previous-day activity (e.g. Uber home at
            # midnight), not a trip departure — discard it.
            _MAX_DEP_GAP = 4 * 3600
            chosen_dep = transport_svc_dep if transport_svc_dep is not None else fuel_dep
            if chosen_dep is not None:
                dep_gap = trip_start_ts - int(valid[chosen_dep]["date"])
                if dep_gap <= _MAX_DEP_GAP:
                    ext = valid[chosen_dep:fp] + ext

        # --- Arrival ---
        # Scan ALL rows within 24 h after the trip's last check-in for the first
        # home-city transport hub.  We intentionally do NOT stop at intermediate
        # non-home cities: on the return leg there are often fuel stops or
        # highway check-ins near Brest (or another transit city) between the
        # border crossing and home.  The 24 h cap and "nearest hub" (break on
        # first found) prevent accidentally absorbing a subsequent departure hub.
        trip_end_ts = int(trip_rows[-1]["date"])
        arr_hub: int | None = None
        i = lp + 1
        while i < len(valid):
            if int(valid[i]["date"]) - trip_end_ts > _24H:
                break
            row_city = valid[i].get("city", "").strip()
            if row_city == home_city and _is_home_transport(valid[i], home_city):
                arr_hub = i  # nearest hub — stop here
                break
            i += 1
        if arr_hub is not None:
            # Include all rows between trip end and hub (inclusive)
            ext = ext + valid[lp + 1 : arr_hub + 1]
        else:
            # Fallback: if no transport hub found, look for a Neighborhood
            # check-in in the home city (e.g. car trip arriving back in a
            # residential area with no fuel stop).
            # Abort if a "Home (private)" check-in is found first — already
            # home, no need to extend.  Also stop at any non-home, non-blank
            # city to avoid absorbing the start of a subsequent trip.
            i = lp + 1
            while i < len(valid):
                if int(valid[i]["date"]) - trip_end_ts > _24H:
                    break
                row_city = valid[i].get("city", "").strip()
                if row_city not in ("", home_city):
                    break  # hit a different city — stop
                if row_city == home_city:
                    cat = valid[i].get("category", "").strip()
                    if cat == "Home (private)":
                        break  # already home — no Neighborhood extension needed
                    if cat == "Neighborhood":
                        arr_hub = i
                        break
                i += 1
            if arr_hub is not None:
                ext = ext + valid[lp + 1 : arr_hub + 1]

        # --- Forced end override ---
        # If this trip's (post-extension) start_ts is in trip_end_overrides,
        # include all rows up to (and including) the specified end timestamp.
        if trip_end_overrides:
            ext_start_ts = int(ext[0]["date"])
            if ext_start_ts in trip_end_overrides:
                force_end_ts = trip_end_overrides[ext_start_ts]
                cur_lp = pos[id(ext[-1])]
                j = cur_lp + 1
                while j < len(valid) and int(valid[j]["date"]) <= force_end_ts:
                    ext.append(valid[j])
                    j += 1

        # --- Forced start override ---
        # If this trip's (post-extension) start_ts is in trip_start_overrides,
        # prepend all rows from the specified start timestamp up to the current
        # trip start.  Useful when the real departure has no transport hub but
        # the home-city morning check-ins should still be included.
        if trip_start_overrides:
            ext_start_ts = int(ext[0]["date"])
            if ext_start_ts in trip_start_overrides:
                force_start_ts = trip_start_overrides[ext_start_ts]
                cur_fp = pos[id(ext[0])]
                prepend = []
                j = cur_fp - 1
                while j >= 0 and int(valid[j]["date"]) >= force_start_ts:
                    prepend.insert(0, valid[j])
                    j -= 1
                ext = prepend + ext

        # --- Bicycle departure extension ---
        # For trips tagged "bicycle", scan backwards from the current trip start
        # and prepend check-ins in outdoor/sports categories that have city ==
        # home_city (blank-city bike-path check-ins that GPS-resolved to home).
        # Stops at the first non-outdoor home check-in or after 24 h lookback.
        current_start_ts = int(ext[0]["date"])
        current_tags = (trip_tags or {}).get(current_start_ts, [])
        if "bicycle" in current_tags:
            cur_fp = pos[id(ext[0])]
            _BIKE_DEP_WINDOW = 4 * 3600  # max seconds before trip start to look for departure
            # Scan backward through home-city rows within the departure window,
            # collecting them regardless of category (intermediate check-ins
            # like sculptures or plazas passed while cycling are fine).
            # Stop at: non-home non-blank city, >4 h gap, or prev_end_idx.
            bike_window: list[int] = []  # indices in valid, chronological order
            j = cur_fp - 1
            while j >= 0:
                if j <= prev_end_idx:
                    break
                row = valid[j]
                row_ts = int(row["date"])
                if current_start_ts - row_ts > _BIKE_DEP_WINDOW:
                    break
                row_city = row.get("city", "").strip()
                if row_city == home_city:
                    bike_window.insert(0, j)  # keep in chronological order
                    j -= 1
                elif row_city == "":
                    j -= 1  # skip blank-city rows silently
                else:
                    break  # different non-blank city — stop
            # Find the earliest home-city row in the window that is a passthrough
            # category (Road, Park, Trail, …).  Include all rows from that anchor
            # to the trip start — intermediate non-passthrough categories (e.g.
            # sculptures, plazas encountered while cycling) are fine.
            anchor: int | None = None
            for idx in bike_window:
                if valid[idx].get("category", "").strip() in _BICYCLE_PASSTHROUGH_CATS:
                    anchor = idx
                    break
            if anchor is not None:
                ext = list(valid[anchor:cur_fp]) + ext

        # --- Home arrival extension ---
        # Scan forward from the current trip end (arr_hub or last non-home check-in)
        # looking for a Home (private) check-in in home_city.
        #
        # Rules (checked per row):
        #   • Non-home, non-blank city → new trip territory; stop, don't extend.
        #   • Nightlife category (bar, pub, etc.) → afterparty; stop, don't extend.
        #     The trip already ends safely at the transport hub (arr_hub) or the
        #     last non-home check-in — nothing after the nightlife is included.
        #   • Home (private) in home_city → found; extend to here and stop.
        #   • Anything else (metro, road, grocery, blank) → mundane return; keep scanning.
        #   • Hard cap: 12 h after current trip end.
        #
        # Window depends on whether a transport hub was found on arrival:
        #   transit return (arr_hub found): 5 h — user is already in the city;
        #     a direct home journey should complete quickly.  Longer gaps mean
        #     the user did a full day of activities before going home (e.g.
        #     returned from Moscow on a weekday morning and went straight to
        #     work) — in that case the trip ends at the hub.
        #   car / local return (no arr_hub): 12 h — the last non-home check-in
        #     can be far from home (e.g. a lake near Lepel at 09:05, arriving
        #     home at 15:55 after the minibus + Minsk commute).
        _NIGHTLIFE_CATS = frozenset({
            "Bar", "Beach Bar", "Beer Bar", "Brewery", "Cocktail Bar",
            "Dive Bar", "Gastropub", "Hookah Bar", "Hotel Bar", "Irish Pub",
            "Karaoke Bar", "Lounge", "Night Club", "Pub", "Rock Club",
            "Sports Bar", "Whisky Bar", "Wine Bar",
        })
        # Transit check-ins that are clearly part of a return drive home.
        # Allow these even when their city is non-blank and non-home —
        # they don't signal new-trip territory.
        _ROADSIDE_CATS = frozenset({
            "Fuel Station", "Gas Station", "Rest Stop", "Truck Stop",
            "Road", "Highway",
        })
        _MAX_HOME_ARRIVAL = 5 * 3600 if arr_hub is not None else 12 * 3600
        cur_end_ts = int(ext[-1]["date"])
        cur_end_idx = pos[id(ext[-1])]
        home_idx: int | None = None
        j = cur_end_idx + 1
        while j < len(valid):
            row_ts = int(valid[j]["date"])
            if row_ts - cur_end_ts > _MAX_HOME_ARRIVAL:
                break
            row_city = valid[j].get("city", "").strip()
            row_cat  = valid[j].get("category", "").strip()
            if row_city and row_city != home_city and row_cat not in _ROADSIDE_CATS:
                break  # non-home city → new trip
            if row_cat in _NIGHTLIFE_CATS:
                break  # afterparty → stop before it
            if row_city == home_city and row_cat == "Home (private)":
                home_idx = j
                break
            j += 1
        if home_idx is not None:
            ext = ext + valid[cur_end_idx + 1 : home_idx + 1]

        extended.append((ext, current_tags, current_start_ts))

    result: list[dict] = []
    for trip_rows, _resolved_tags, _name_ts in extended:
        if len(trip_rows) < min_checkins:
            continue

        dates = []
        for r in trip_rows:
            d = _parse_ts(r)
            if d:
                dates.append(d)
        if not dates:
            continue

        countries_c = Counter(
            r.get("country", "").strip() for r in trip_rows if r.get("country", "").strip()
        )
        cities_c = Counter(
            r.get("city", "").strip() for r in trip_rows if r.get("city", "").strip()
        )
        top_countries = [c for c, _ in countries_c.most_common()]
        top_cities    = [c for c, _ in cities_c.most_common(3)]

        if len(top_countries) == 1:
            name = f"{top_cities[0] if top_cities else top_countries[0]}, {top_countries[0]}"
        elif len(top_countries) == 2:
            name = " & ".join(top_countries[:2])
        else:
            name = f"{top_countries[0]} + {top_countries[1]} + {len(top_countries) - 2} more"

        # Apply custom name override (keyed by start_ts of first check-in)
        # Use the pre-extension start_ts for name lookup so bicycle departure
        # extension (which prepends outdoor home check-ins) doesn't break names.
        start_ts_key = str(_name_ts)
        if trip_names and start_ts_key in trip_names:
            name = trip_names[start_ts_key]

        checkins: list[dict] = []
        for r in trip_rows:
            d = _parse_ts(r)
            if not d:
                continue
            try:
                lat = round(float(r["lat"]), 5)
            except (ValueError, KeyError, TypeError):
                lat = None
            try:
                lng = round(float(r["lng"]), 5)
            except (ValueError, KeyError, TypeError):
                lng = None
            d_local = _localise(d, lat, lng, r.get("country", "").strip())
            checkins.append(
                {
                    "ts":         int(r["date"]),
                    "date":       d_local.strftime("%Y-%m-%d"),
                    "time":       d_local.strftime("%H:%M"),
                    "datetime":   d_local.strftime("%d %b %Y, %H:%M"),
                    "venue":      r.get("venue", "").strip(),
                    "venue_id":   r.get("venue_id", "").strip(),
                    "city":       r.get("city", "").strip(),
                    "country":    r.get("country", "").strip(),
                    "category":   r.get("category", "").strip(),
                    "lat":        lat,
                    "lng":        lng,
                    "checkin_id": r.get("checkin_id", "").strip(),
                }
            )

        seen_v: set[str] = set()
        unique_pts: list = []
        for r in trip_rows:
            vid = r.get("venue_id", "").strip()
            if vid and vid not in seen_v:
                seen_v.add(vid)
                try:
                    unique_pts.append(
                        [round(float(r["lat"]), 5), round(float(r["lng"]), 5), r.get("venue", "").strip()]
                    )
                except (ValueError, KeyError, TypeError):
                    pass

        trip_cats = Counter(
            r.get("category", "").strip() for r in trip_rows if r.get("category", "").strip()
        )
        if checkins:
            from datetime import date as _date
            _sd = _date.fromisoformat(checkins[0]["date"])
            _ed = _date.fromisoformat(checkins[-1]["date"])
            start_date = checkins[0]["date"]
            end_date   = checkins[-1]["date"]
            start_year = _sd.year
            duration   = (_ed - _sd).days + 1
        else:
            start_date = str(dates[0].date())
            end_date   = str(dates[-1].date())
            start_year = dates[0].year
            duration   = (dates[-1].date() - dates[0].date()).days + 1
        result.append(
            {
                "name":           name,
                "_name_ts":       _name_ts,
                "start_date":     start_date,
                "end_date":       end_date,
                "start_ts":       int(trip_rows[0]["date"]),
                "start_year":     start_year,
                "duration":       duration,
                "countries":      top_countries,
                "cities":         [c for c, _ in cities_c.most_common()],
                "checkin_count":  len(trip_rows),
                "unique_places":  len(seen_v),
                "checkins":       checkins,
                "top_cats":       [[c, n] for c, n in trip_cats.most_common(10)],
                "tags":           _resolved_tags if _resolved_tags else (trip_tags or {}).get(int(trip_rows[0]["date"]), []),
            }
        )

    result.sort(key=lambda t: t["start_ts"])
    if trip_exclude:
        result = [t for t in result if t["start_ts"] not in trip_exclude]
    for i, t in enumerate(result):
        t["id"] = i + 1
    return result


# ── Shout text mining ──────────────────────────────────────────────────────────

import re as _re  # noqa: E402 — section-local import for the shout-mining block

# Stopwords across the languages that appear in shouts (en, ru, be).  Kept
# minimal and additive — false-positives just leak a stopword into the
# top-N list, which is annoying but not broken.
_STOPWORDS: frozenset[str] = frozenset({
    # English
    "the","a","an","and","or","but","of","to","for","in","on","at","by","with",
    "is","was","are","be","been","being","am","i","my","me","mine","we","our",
    "you","your","he","she","it","its","they","them","their","this","that",
    "these","those","as","if","then","than","so","such","just","not","no",
    "yes","do","does","did","done","have","has","had","will","would","could",
    "should","can","may","might","get","got","make","made","go","went","gone",
    "from","up","down","out","off","over","under","again","still","more",
    "less","most","least","very","too","also","only","own","other","some",
    "any","all","each","every","new","old","good","bad","one","two","three",
    "first","last","time","day","now","here","there","what","who","when",
    "where","why","how","im","ill","ive","its","dont","didnt","cant","wont",
    # Russian
    "и","в","во","не","что","он","на","я","с","со","как","а","то","все",
    "она","так","его","но","да","ты","к","у","же","вы","за","бы","по","только",
    "ее","мне","было","вот","от","меня","еще","нет","о","из","ему","теперь",
    "когда","даже","ну","вдруг","ли","если","уже","или","ни","быть","был",
    "него","до","вас","нибудь","опять","уж","вам","ведь","там","потом","себя",
    "ничего","ей","может","они","тут","где","есть","надо","ней","для","мы",
    "тебя","их","чем","была","сам","чтоб","без","будто","чего","раз","тоже",
    "себе","под","будет","ж","тогда","кто","этот","того","потому","этого",
    "какой","совсем","ним","здесь","этом","один","почти","мой","тем","чтобы",
    "нее","сейчас","были","куда","зачем","всех","никогда","можно","при",
    "наконец","два","об","другой","хоть","после","над","больше","тот","через",
    "эти","нас","про","всего","них","какая","много","разве","три","эту",
    "моя","впрочем","хорошо","свою","этой","перед","иногда","лучше","чуть",
    "том","нельзя","такой","им","более","всегда","конечно","всю","между",
    # Belarusian (additions)
    "у","і","на","ў","да","не","з","ад","па","за","для","пра","над","пад",
    "цераз","праз","між","без","пры","к","к","і","ці","альбо","ёсць","няма",
    "быў","была","было","былі","можа","трэба","тут","там","гэта","гэты",
    "тая","той","той","той","ужо","яшчэ","нават","толькі","мы","вы","ён","яна",
    "яно","яны","свой","свая","сваё","свае",
})

# Token regex: keep unicode letters/digits, length ≥ 3.
_TOKEN_RE = _re.compile(r"[\w']{3,}", _re.UNICODE)

# Emoji extraction — covers the major unicode emoji blocks (not exhaustive
# but catches >99% of what people actually type).  Uses str scan rather than
# regex range because Python's re module groks Unicode codepoint ranges
# inconsistently across platforms.
_EMOJI_RANGES = (
    (0x1F300, 0x1F5FF),  # Misc symbols and pictographs
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F680, 0x1F6FF),  # Transport and map
    (0x1F700, 0x1F77F),
    (0x1F780, 0x1F7FF),
    (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF),  # Supplemental symbols and pictographs
    (0x1FA00, 0x1FA6F),
    (0x1FA70, 0x1FAFF),
    (0x2600,  0x26FF),   # Misc symbols
    (0x2700,  0x27BF),   # Dingbats
)

def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)

def _extract_emojis(text: str) -> list[str]:
    return [ch for ch in text if _is_emoji_char(ch)]

# Light emoji sentiment lexicon — language-independent.  Conservative: only
# include emojis whose polarity is unambiguous.  Counted at character level.
_EMOJI_POS = frozenset("👍😀😁😂😃😄😅😆😊😍🥰😘🤩🥳🎉🎊🍻🥂🌟⭐❤💖💕💗💜💛💚💙🧡🍰🍕🍔🍣🍜🍱🍷🌅🌄🌞🏖🏝🏔🚀")
_EMOJI_NEG = frozenset("👎😞😢😭😱😡🤬💔😤😨😰😖😣😩😫😒🙄☹😟🤢🤮")

def _detect_lang(text: str) -> str:
    """Return 'cyr' if cyrillic dominates, 'lat' if latin, else 'mix'."""
    cyr = lat = 0
    for ch in text:
        if 'А' <= ch <= 'я' or ch in 'ЁёІіЎў':
            cyr += 1
        elif 'a' <= ch.lower() <= 'z':
            lat += 1
    if cyr == 0 and lat == 0:
        return "other"
    if cyr > lat * 2:
        return "cyr"
    if lat > cyr * 2:
        return "lat"
    return "mix"

def shout_analysis(rows: list[dict]) -> dict:
    """Mine the free-text 'shout' column for words, language, sentiment.

    Operates over the same filtered set used by shout_records() so the
    `total_shouts` count on the stats page matches the Shouts page count.
    """
    # Build a denylist of companion-name tokens (lower-cased single words).
    name_deny: set[str] = set()
    for r in rows:
        for col in ("with_name", "created_by_name", "overlaps_name"):
            raw = (r.get(col) or "").replace(" ,", ",")
            for part in raw.split(","):
                for tok in part.strip().split():
                    t = tok.strip().lower()
                    if len(t) >= 3 and t != "-":
                        name_deny.add(t)

    # Reuse the records pipeline so the filtered set is identical.
    records = shout_records(rows)
    shouts: list[tuple[int, str, str, str]] = []  # (year, country, city, text)
    for rec in records:
        try:
            yr = datetime.fromtimestamp(rec["ts"], tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        shouts.append((yr, rec.get("country", ""), rec.get("city", ""), rec["text"]))

    if not shouts:
        return {}

    # Word frequency — global + per country (companion names denied)
    word_ctr_global: Counter = Counter()
    word_ctr_country: dict[str, Counter] = defaultdict(Counter)
    for _yr, co, _cy, text in shouts:
        toks = [t.lower() for t in _TOKEN_RE.findall(text)]
        toks = [t for t in toks
                if t not in _STOPWORDS
                and t not in name_deny
                and not t.isdigit()]
        word_ctr_global.update(toks)
        if co:
            word_ctr_country[co].update(toks)

    # Emoji frequency
    emoji_ctr: Counter = Counter()
    emoji_by_year: dict[int, Counter] = defaultdict(Counter)
    for yr, _co, _cy, text in shouts:
        for e in _extract_emojis(text):
            emoji_ctr[e] += 1
            emoji_by_year[yr][e] += 1

    # Emoji-based sentiment per year — share of pos vs neg emoji-bearing shouts
    sentiment_by_year: dict[int, list] = defaultdict(lambda: [0, 0, 0])  # [pos, neg, neutral]
    for yr, _co, _cy, text in shouts:
        pos = sum(1 for ch in text if ch in _EMOJI_POS)
        neg = sum(1 for ch in text if ch in _EMOJI_NEG)
        if pos > neg:
            sentiment_by_year[yr][0] += 1
        elif neg > pos:
            sentiment_by_year[yr][1] += 1
        else:
            sentiment_by_year[yr][2] += 1

    # Language distribution per year (cyr/lat/mix/other)
    lang_by_year: dict[int, Counter] = defaultdict(Counter)
    for yr, _co, _cy, text in shouts:
        lang_by_year[yr][_detect_lang(text)] += 1

    # Per-year shout count and avg length
    per_year: dict[int, list] = defaultdict(lambda: [0, 0])  # [count, total_chars]
    for yr, _co, _cy, text in shouts:
        per_year[yr][0] += 1
        per_year[yr][1] += len(text)

    # Top words per country — top 10 countries by shout volume, top 12 words each
    shouts_per_country: Counter = Counter(co for _yr, co, _cy, _t in shouts if co)
    top_cos = [co for co, _ in shouts_per_country.most_common(10)]
    words_by_country: list = []
    for co in top_cos:
        top12 = word_ctr_country[co].most_common(12)
        if top12:
            words_by_country.append([co, [[w, c] for w, c in top12]])

    return {
        "total_shouts":   len(shouts),
        "total_words":    sum(word_ctr_global.values()),
        "top_words":      [[w, c] for w, c in word_ctr_global.most_common(60)],
        "top_emojis":     [[e, c] for e, c in emoji_ctr.most_common(40)],
        "shouts_per_year": sorted([[str(yr), v[0], round(v[1] / v[0])] for yr, v in per_year.items()]),
        "sentiment_by_year": sorted([[str(yr), v[0], v[1], v[2]] for yr, v in sentiment_by_year.items()]),
        "lang_by_year":   sorted([[str(yr), ctr.get("lat", 0), ctr.get("cyr", 0), ctr.get("mix", 0), ctr.get("other", 0)] for yr, ctr in lang_by_year.items()]),
        "words_by_country": words_by_country,
    }


# ── Shout records (raw list for dedicated page) ───────────────────────────────

def collect_companions(row: dict) -> list[str]:
    """De-duplicated list of companion names from all three Foursquare signals.

    - `with_name`: explicitly tagged ("with Joanna")
    - `created_by_name`: someone else checked you in
    - `overlaps_name`: a friend independently checked in to the same venue
      around the same time

    Names are compared case-insensitively to dedupe; the first-seen original
    casing wins.  The Foursquare overlaps column uses "-" as a sentinel for
    "none" — those are excluded.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _push(raw: str, allow_dash_sentinel: bool = False) -> None:
        for part in (raw or "").replace(" ,", ",").split(","):
            n = part.strip()
            if not n:
                continue
            if not allow_dash_sentinel and n == "-":
                continue
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(n)

    _push(row.get("with_name") or "")
    cb = (row.get("created_by_name") or "").strip()
    if cb and cb.lower() not in seen:
        seen.add(cb.lower())
        out.append(cb)
    _push(row.get("overlaps_name") or "")
    return out


# Module-level so build.py / gen_shouts can reuse the same suffix regex.
_SHOUT_SUFFIX_RE = _re.compile(r"\s*[—\-–]\s*with\s+.+$", _re.IGNORECASE | _re.UNICODE)
# Matches shouts whose ENTIRE text is just "with X" companion attribution
# (with or without a leading dash). ~14K of the 18K shouts are this pattern.
_SHOUT_WITH_ONLY_RE = _re.compile(r"^\s*[—\-–]?\s*with\s+\S+", _re.IGNORECASE | _re.UNICODE)

def _build_companion_denylist(rows: list[dict]) -> set[str]:
    """Collect every companion name (full + first-name) seen in the rows.

    Used to drop shouts whose text is just a bare companion name like
    "Joanna" (~190 such rows) — Foursquare stores attribution without the
    "with" prefix sometimes, so the regex above misses them.
    """
    names: set[str] = set()
    for r in rows:
        for col in ("with_name", "created_by_name", "overlaps_name"):
            raw = (r.get(col) or "").replace(" ,", ",")
            for part in raw.split(","):
                n = part.strip()
                if not n or n == "-" or len(n) < 2:
                    continue
                names.add(n.lower())
                first = n.split()[0] if n.split() else ""
                if first:
                    names.add(first.lower())
    return names


def shout_records(rows: list[dict]) -> list[dict]:
    """Return check-ins that carry a real text shout, sorted newest-first.

    Filters keep only substantive content:
      1. Strip trailing " — with X" companion suffix.
      2. Drop shouts whose entire content is just "with X" attribution.
      3. Drop bare companion-name shouts ("Joanna", "Максим") — same
         attribution data leaked in without the "with" prefix.
      4. Drop pure-punctuation shouts (".", "!", "?") which have no signal.
    """
    companion_names = _build_companion_denylist(rows)
    # Tokenise candidate text into words for the bare-name check.
    word_re = _re.compile(r"[^\W_]+", _re.UNICODE)

    out: list[dict] = []
    for r in rows:
        s = (r.get("shout") or "").strip()
        if not s:
            continue
        clean = _SHOUT_SUFFIX_RE.sub("", s).strip()
        if not clean:
            continue
        # Skip pure companion-attribution shouts ("with Joanna", "— with Tata")
        if _SHOUT_WITH_ONLY_RE.match(clean):
            continue
        # Skip if every alphanumeric token is itself a known companion name
        # (covers bare "Joanna", "Максим", "NIkita Tata" etc.)
        toks = word_re.findall(clean)
        if toks and all(t.lower() in companion_names for t in toks):
            continue
        # Skip pure-punctuation shouts (no word tokens at all)
        if not toks:
            continue
        try:
            ts = int(r["date"])
        except (ValueError, KeyError, TypeError):
            continue
        out.append({
            "ts":         ts,
            "text":       clean,
            "venue":      (r.get("venue") or "").strip(),
            "venue_id":   (r.get("venue_id") or "").strip(),
            "city":       (r.get("city") or "").strip(),
            "country":    (r.get("country") or "").strip(),
            "category":   (r.get("category") or "").strip(),
            "companions": collect_companions(r),
            "lat":        r.get("lat") or None,
            "lng":        r.get("lng") or None,
            "checkin_id": (r.get("checkin_id") or "").strip(),
        })
    out.sort(key=lambda x: -x["ts"])
    return out


def merge_comments_into_shouts(
    shouts: list[dict], rows: list[dict], comments_map: dict
) -> list[dict]:
    """Return a shouts-page list enriched with per-check-in comment threads.

    `comments_map` is the `comments` object from comments.json: {checkin_id:
    {venue, venue_id, ts, items:[{text, at, author, author_id}], …}}. Comments
    are the reply thread posted *under* a check-in — distinct from its `shout`.

    Behaviour:
      1. Every shout entry gets a `comments` list (its thread, or []).
      2. Check-ins that have comments but NO shout are added as new entries
         (text=""), so the archive surfaces every comment thread, not just the
         ones that happen to sit under a shout.
    Original `shouts` dicts are not mutated. Result is sorted newest-first.
    """
    comments_map = comments_map or {}

    def _thread(cid: str) -> list[dict]:
        return list((comments_map.get(cid) or {}).get("items") or [])

    out: list[dict] = []
    shout_ids: set[str] = set()
    for s in shouts:
        cid = (s.get("checkin_id") or "").strip()
        shout_ids.add(cid)
        entry = dict(s)
        entry["comments"] = _thread(cid)
        out.append(entry)

    # Comment-only check-ins (have a thread but no qualifying shout). Pull venue/
    # city/country/category/companions from the check-in row when we have it,
    # else fall back to the venue/ts stored alongside the comments.
    row_by_id = {(r.get("checkin_id") or "").strip(): r for r in rows}
    for cid, meta in comments_map.items():
        cid = (cid or "").strip()
        if not cid or cid in shout_ids:
            continue
        items = _thread(cid)
        if not items:
            continue
        r = row_by_id.get(cid)
        if r is not None:
            try:
                ts = int(r["date"])
            except (ValueError, KeyError, TypeError):
                ts = int(meta.get("ts") or 0)
            out.append({
                "ts":         ts,
                "text":       "",
                "venue":      (r.get("venue") or "").strip(),
                "venue_id":   (r.get("venue_id") or "").strip(),
                "city":       (r.get("city") or "").strip(),
                "country":    (r.get("country") or "").strip(),
                "category":   (r.get("category") or "").strip(),
                "companions": collect_companions(r),
                "checkin_id": cid,
                "comments":   items,
            })
        else:
            out.append({
                "ts":         int(meta.get("ts") or 0),
                "text":       "",
                "venue":      (meta.get("venue") or "").strip(),
                "venue_id":   (meta.get("venue_id") or "").strip(),
                "city":       "",
                "country":    "",
                "category":   "",
                "companions": [],
                "checkin_id": cid,
                "comments":   items,
            })

    out.sort(key=lambda x: -x["ts"])
    return out


# ── Cross-dimensional analytics ────────────────────────────────────────────────

def cross_dim_analysis(rows: list[dict], categorize) -> dict:
    """Hour-of-day and day-of-week breakdowns per category group, in local time."""
    # Pick top 8 category groups by overall volume so the heatmap stays legible.
    grp_ctr: Counter = Counter()
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        g = categorize(cat)
        if g:
            grp_ctr[g] += 1
    top_groups = [g for g, _ in grp_ctr.most_common(8)]
    if not top_groups:
        return {}
    g_idx = {g: i for i, g in enumerate(top_groups)}

    # hour_cat[hour][group_idx] = count;  dow_cat[dow][group_idx] = count
    hour_cat = [[0] * len(top_groups) for _ in range(24)]
    dow_cat  = [[0] * len(top_groups) for _ in range(7)]
    country_hour = defaultdict(lambda: [0] * 24)  # country → 24-hour profile

    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        g = categorize(cat)
        if g not in g_idx:
            continue
        try:
            d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
        except (ValueError, KeyError, OSError):
            continue
        try:
            lat = float(r["lat"]); lng = float(r["lng"])
        except (ValueError, KeyError, TypeError):
            lat = lng = None
        country = r.get("country", "").strip()
        d_local = _localise(d, lat, lng, country)
        h = d_local.hour
        dw = d_local.weekday()
        idx = g_idx[g]
        hour_cat[h][idx] += 1
        dow_cat[dw][idx] += 1
        if country:
            country_hour[country][h] += 1

    # Country-hour: top 12 countries by volume, normalized so each row sums to 100.
    top_cos = [c for c, _ in Counter(
        {k: sum(v) for k, v in country_hour.items()}
    ).most_common(12)]
    country_hour_pct = []
    for co in top_cos:
        row = country_hour[co]
        total = sum(row) or 1
        country_hour_pct.append([co, [round(v * 100 / total, 1) for v in row]])

    return {
        "groups":           top_groups,
        "hour_cat":         hour_cat,
        "dow_cat":          dow_cat,
        "country_hour_pct": country_hour_pct,
    }


# ── Main aggregation ───────────────────────────────────────────────────────────

def process(
    rows: list[dict],
    mappings: dict[str, Any],
    home_city: str = "Minsk",
    min_trip_checkins: int = 5,
    trip_names: dict[str, str] | None = None,
    trip_exclude: set[int] | None = None,
    trip_end_overrides: dict[int, int] | None = None,
    trip_start_overrides: dict[int, int] | None = None,
    trip_tags: dict[int, list[str]] | None = None,
    new_country_year_overrides: dict[str, int] | None = None,
) -> tuple[dict, list[dict]]:
    """
    Compute all dashboard metrics from pre-transformed rows.
    Returns (stats_dict, trips_list).
    """
    categorize     = build_categorize_fn(mappings.get("category_groups", {}))
    explorer_groups: dict[str, list[str]] = mappings.get("explorer_groups", {})

    # ── Core counters ─────────────────────────────────────────────────────────
    dates: list[datetime] = []
    for r in rows:
        d = _parse_ts(r)
        if d:
            dates.append(d)

    countries  = Counter(r["country"] for r in rows if r.get("country", "").strip())
    cities     = Counter(r["city"]    for r in rows if r.get("city", "").strip())

    # ── City → primary country mapping ───────────────────────────────────────
    city_country_ctr: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        cy = r.get("city",    "").strip()
        co = r.get("country", "").strip()
        if cy and co:
            city_country_ctr[cy][co] += 1
    city_primary_country: dict[str, str] = {
        cy: ctr.most_common(1)[0][0]
        for cy, ctr in city_country_ctr.items()
    }

    # ── City centroids (average lat/lng per city, for city dot map) ──────────
    _city_coords: dict[str, list] = defaultdict(list)
    for r in rows:
        cy = r.get("city", "").strip()
        if not cy:
            continue
        try:
            _city_coords[cy].append((float(r["lat"]), float(r["lng"])))
        except (ValueError, KeyError, TypeError):
            pass
    city_centroids: dict[str, list] = {}
    for cy, pts in _city_coords.items():
        if pts:
            city_centroids[cy] = [
                round(sum(p[0] for p in pts) / len(pts), 3),
                round(sum(p[1] for p in pts) / len(pts), 3),
            ]

    # ── Country centroids (average lat/lng per country) ───────────────────────
    _cc_coords: dict[str, list] = defaultdict(list)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        try:
            _cc_coords[co].append((float(r["lat"]), float(r["lng"])))
        except (ValueError, KeyError, TypeError):
            pass
    country_centroids: dict[str, list] = {}
    for co, pts in _cc_coords.items():
        if pts:
            country_centroids[co] = [
                round(sum(p[0] for p in pts) / len(pts), 3),
                round(sum(p[1] for p in pts) / len(pts), 3),
                len(pts),
            ]
    by_year    = Counter(d.year for d in dates)
    by_month   = Counter((d.year, d.month) for d in dates)
    by_hour    = Counter(d.hour for d in dates)
    by_dow     = Counter(d.weekday() for d in dates)

    # ── Venues: unique by venue_id with city ─────────────────────────────────
    venue_by_id: dict[str, dict] = {}
    for r in rows:
        vid  = r.get("venue_id", "").strip()
        name = r.get("venue",    "").strip()
        city = r.get("city",     "").strip()
        if not (vid and name):
            continue
        if vid not in venue_by_id:
            venue_by_id[vid] = {"name": name, "city": city, "count": 0, "id": vid}
        venue_by_id[vid]["count"] += 1
    venues_top500 = sorted(venue_by_id.values(), key=lambda x: -x["count"])[:500]
    venues_list   = [[v["name"], v["count"], v["city"], v.get("id","")] for v in venues_top500]

    # ── Category groups ───────────────────────────────────────────────────────
    cat_groups: Counter = Counter()
    for r in rows:
        cat = r.get("category", "").strip()
        if cat:
            grp = categorize(cat)
            if grp:
                cat_groups[grp] += 1

    # ── Category Explorer: unique by venue_id ────────────────────────────────
    cat_vid: dict[str, dict] = defaultdict(dict)
    for r in rows:
        cat   = r.get("category", "").strip()
        vid   = r.get("venue_id", "").strip()
        venue = r.get("venue",    "").strip()
        city  = r.get("city",     "").strip()
        if not (cat and vid and venue):
            continue
        if vid not in cat_vid[cat]:
            cat_vid[cat][vid] = {"name": venue, "city": city, "count": 0}
        cat_vid[cat][vid]["count"] += 1

    explorer: dict[str, list] = {}
    for display_name, raw_cats in explorer_groups.items():
        combined: dict[str, dict] = {}
        for rc in raw_cats:
            for vid, d in cat_vid.get(rc, {}).items():
                if vid not in combined:
                    combined[vid] = {"name": d["name"], "city": d["city"], "count": 0, "vid": vid}
                combined[vid]["count"] += d["count"]
        top50 = sorted(combined.values(), key=lambda x: -x["count"])[:50]
        if top50:
            explorer[display_name] = [[d["name"], d["city"], d["count"], d["vid"]] for d in top50]
    explorer_cats = [k for k in explorer_groups if k in explorer]

    # ── Unique places (enriched: lat, lng, name, count, years, cat, city) ──
    # First pass: accumulate per-venue stats keyed by venue_id
    _vp: dict = {}  # venue_id → {lat, lng, name, count, last_ts, years, category, city}
    _vp_coord: dict = {}  # (lat3,lng3) → same shape, for no-id rows
    for r in rows:
        vid = r.get("venue_id", "").strip()
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
            has_coords = True
        except (ValueError, KeyError, TypeError):
            has_coords = False
        ts = int(r["date"]) if r.get("date") else 0
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else None
        raw_cat = r.get("category", "").strip()
        if vid:
            if vid not in _vp:
                _vp[vid] = {
                    "lat": lat_f if has_coords else 0.0,
                    "lng": lng_f if has_coords else 0.0,
                    "name": r.get("venue", "").strip(),
                    "count": 0, "last_ts": 0, "years": set(),
                    "cat": raw_cat,
                    "city": r.get("city", "").strip(),
                    "has_coords": has_coords,
                }
            e = _vp[vid]
            e["count"] += 1
            if ts > e["last_ts"]:
                e["last_ts"] = ts
            if yr:
                e["years"].add(yr)
            if not e["cat"] and raw_cat:
                e["cat"] = raw_cat
            if not e["city"] and r.get("city", "").strip():
                e["city"] = r.get("city", "").strip()
        elif has_coords:
            key = (round(lat_f, 3), round(lng_f, 3))
            if key not in _vp_coord:
                _vp_coord[key] = {
                    "lat": lat_f, "lng": lng_f,
                    "name": r.get("venue", "").strip(),
                    "count": 0, "last_ts": 0, "years": set(),
                    "cat": raw_cat,
                    "city": r.get("city", "").strip(),
                    "has_coords": True,
                }
            e = _vp_coord[key]
            e["count"] += 1
            if ts > e["last_ts"]:
                e["last_ts"] = ts
            if yr:
                e["years"].add(yr)

    seen_ids: set[str] = set(_vp.keys())
    seen_coords: set[tuple] = set(_vp_coord.keys())
    unique_places: list = []
    for e in _vp.values():
        if not e["has_coords"]:
            continue
        unique_places.append([
            round(e["lat"], 5), round(e["lng"], 5),
            e["name"], e["count"], sorted(e["years"]), e["cat"], e["city"],
        ])
    for e in _vp_coord.values():
        unique_places.append([
            round(e["lat"], 5), round(e["lng"], 5),
            e["name"], e["count"], sorted(e["years"]), e["cat"], e["city"],
        ])

    unique_count = len(seen_ids) + len(seen_coords)

    # ── Countries by venues ───────────────────────────────────────────────────
    country_vids: dict[str, set] = defaultdict(set)
    city_vids:    dict[str, set] = defaultdict(set)
    for r in rows:
        c   = r.get("country", "").strip()
        cy  = r.get("city",    "").strip()
        vid = r.get("venue_id", "").strip() or r.get("venue", "").strip()
        if c and vid:
            country_vids[c].add(vid)
        if cy and vid:
            city_vids[cy].add(vid)
    countries_by_venues = [
        [c, len(v)]
        for c, v in sorted(country_vids.items(), key=lambda kv: -len(kv[1]))
    ]
    cities_by_venues = [
        [cy, len(v), city_primary_country.get(cy, "")]
        for cy, v in sorted(city_vids.items(), key=lambda kv: -len(kv[1]))
    ]

    # ── All coords (kept for dot map) ────────────────────────────────────────
    all_coords: list = []
    for r in rows:
        try:
            all_coords.append([round(float(r["lat"]), 5), round(float(r["lng"]), 5)])
        except (ValueError, KeyError, TypeError):
            pass

    # ── Venues heatmap + per-year + per-catgroup ─────────────────────────────
    import math as _math
    # _vh: venue_id → [lat, lng, total_count, {year: count}, cat_group]
    _vh: dict = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
        except (ValueError, KeyError, TypeError):
            continue
        ts = int(r["date"]) if r.get("date") else 0
        yr = datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else 0
        cg = categorize(r.get("category", "").strip()) or ""
        if vid not in _vh:
            _vh[vid] = [lat_f, lng_f, 0, {}, cg]
        _vh[vid][2] += 1
        if yr:
            _vh[vid][3][yr] = _vh[vid][3].get(yr, 0) + 1
        if not _vh[vid][4] and cg:
            _vh[vid][4] = cg

    _vh_max = _math.log1p(max(v[2] for v in _vh.values())) if _vh else 1.0

    venues_heatmap: list = [
        [v[0], v[1], round(_math.log1p(v[2]) / _vh_max, 4)]
        for v in _vh.values()
    ]

    # Per-year heatmaps: {year: [[lat, lng, w], ...]}
    _yr_counts: dict[int, dict] = {}  # year → {vid: count}
    for vid, v in _vh.items():
        for yr, cnt in v[3].items():
            if yr not in _yr_counts:
                _yr_counts[yr] = {}
            _yr_counts[yr][vid] = cnt
    venues_by_year: dict = {}
    for yr, vc in _yr_counts.items():
        _max = _math.log1p(max(vc.values())) if vc else 1.0
        venues_by_year[str(yr)] = [
            [_vh[vid][0], _vh[vid][1], round(_math.log1p(cnt) / _max, 4)]
            for vid, cnt in vc.items()
        ]

    # Per-catgroup heatmaps: {catgroup: [[lat, lng, w], ...]}
    _cg_counts: dict[str, dict] = {}  # catgroup → {vid: count}
    for vid, v in _vh.items():
        cg = v[4]
        if not cg:
            continue
        if cg not in _cg_counts:
            _cg_counts[cg] = {}
        _cg_counts[cg][vid] = v[2]
    venues_by_catgrp: dict = {}
    for cg, vc in _cg_counts.items():
        _max = _math.log1p(max(vc.values())) if vc else 1.0
        venues_by_catgrp[cg] = [
            [_vh[vid][0], _vh[vid][1], round(_math.log1p(cnt) / _max, 4)]
            for vid, cnt in vc.items()
        ]

    # ── Companions ────────────────────────────────────────────────────────────
    comp_raw: Counter = Counter()
    for r in rows:
        seen_in_row: set = set()
        raw = r.get("with_name", "").strip()
        for name in [n.strip() for n in raw.replace(" ,", ",").split(",")]:
            if name:
                comp_raw[name] += 1
                seen_in_row.add(name)
        # Also count friend-created check-ins (createdBy ≠ owner), deduping against with_name
        cb = r.get("created_by_name", "").strip()
        if cb and cb not in seen_in_row:
            comp_raw[cb] += 1
            seen_in_row.add(cb)
        # Also count overlapping friends (checked in separately at same venue/time)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() != "-"]:
            if name and name not in seen_in_row:
                comp_raw[name] += 1
                seen_in_row.add(name)
    companions = [[n, c] for n, c in comp_raw.most_common(30)]

    # ── Social analytics (Group 4) ────────────────────────────────────────────
    def _has_companion(r: dict) -> bool:
        if r.get("with_name", "").strip():
            return True
        if r.get("created_by_name", "").strip():
            return True
        ov = r.get("overlaps_name", "").strip()
        return bool(ov and ov != "-")

    _social_yr: dict[int, list] = defaultdict(lambda: [0, 0])  # [solo, group]
    for r in rows:
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _social_yr[yr][1 if _has_companion(r) else 0] += 1

    solo_vs_group_by_year = sorted([[str(yr), v[0], v[1]] for yr, v in _social_yr.items()])
    _total_solo  = sum(v[0] for v in _social_yr.values())
    _total_group = sum(v[1] for v in _social_yr.values())
    solo_vs_group_totals = [_total_solo, _total_group]

    # Companions by country — top 15 companions, top 5 countries each
    _comp_cos: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        _seen: set = set()
        for name in [n.strip() for n in r.get("with_name", "").replace(" ,", ",").split(",")]:
            if name and name not in _seen:
                _comp_cos[name][co] += 1
                _seen.add(name)
        cb = r.get("created_by_name", "").strip()
        if cb and cb not in _seen:
            _comp_cos[cb][co] += 1
            _seen.add(cb)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() != "-"]:
            if name and name not in _seen:
                _comp_cos[name][co] += 1
                _seen.add(name)

    _top15 = [n for n, _ in comp_raw.most_common(15)]
    companion_countries = [
        [name, [[co, cnt] for co, cnt in _comp_cos[name].most_common()]]
        for name in _top15 if name in _comp_cos
    ]

    # ── Day heatmap ───────────────────────────────────────────────────────────
    heatmap: dict[str, dict] = defaultdict(dict)
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        yr  = str(d.year)
        heatmap[yr][key] = heatmap[yr].get(key, 0) + 1
    heatmap = dict(sorted(heatmap.items()))

    # ── Discovery rate ────────────────────────────────────────────────────────
    _seen_disc: set[str] = set()
    _mon: dict[str, list] = defaultdict(lambda: [0, 0])
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        vid = r.get("venue_id", "").strip() or r.get("venue", "").strip()
        if not vid:
            continue
        try:
            d   = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            key = f"{d.year}-{d.month:02d}"
        except (ValueError, OSError):
            continue
        if vid not in _seen_disc:
            _seen_disc.add(vid)
            _mon[key][0] += 1
        else:
            _mon[key][1] += 1
    discovery_rate = sorted([[k, v[0], v[1]] for k, v in _mon.items()])

    # ── Venue loyalty ─────────────────────────────────────────────────────────
    _vy: dict[str, set] = defaultdict(set)
    _vi: dict[str, tuple] = {}
    _vc: Counter = Counter()
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _vy[vid].add(yr)
        _vc[vid] += 1
        if vid not in _vi:
            _vi[vid] = (r.get("venue", "").strip(), r.get("city", "").strip())
    loyal: list = []
    for vid, yrs in _vy.items():
        if len(yrs) >= 3:
            nm, cy = _vi[vid]
            loyal.append([nm, cy, sorted(yrs), _vc[vid]])
    loyal.sort(key=lambda x: (-len(x[2]), -x[3]))
    venue_loyalty = loyal[:100]

    # ── Venue visit-frequency distribution ───────────────────────────────────
    # How many venues were visited exactly once, twice, …
    _vvc: Counter = Counter(_vc.values())
    _freq_buckets = [(1,'1'),(2,'2'),(3,'3'),(4,'4'),(5,'5'),(10,'6–10'),(20,'11–20'),(50,'21–50'),(999999,'50+')]
    venue_freq_dist: list = []
    _prev = 0
    for _upper, _lbl in _freq_buckets:
        _n = sum(cnt for v, cnt in _vvc.items() if _prev < v <= _upper)
        if _n:
            venue_freq_dist.append([_lbl, _n])
        _prev = _upper

    # ── Regulars: top venues by distinct calendar months visited ──────────────
    _reg_months: dict[str, set] = defaultdict(set)
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            _d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            _reg_months[vid].add((_d.year, _d.month))
        except (ValueError, OSError):
            pass
    _regulars_raw: list = []
    for vid, _months in _reg_months.items():
        if len(_months) < 3:
            continue
        _nm, _cy = _vi.get(vid, ("", ""))
        if _nm:
            _regulars_raw.append([_nm, _cy, len(_months), _vc[vid], vid])
    _regulars_raw.sort(key=lambda x: (-x[2], -x[3]))
    venue_regulars = _regulars_raw[:30]

    # ── Revisit interval histogram ────────────────────────────────────────────
    _venue_ts: dict[str, list] = defaultdict(list)
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            _venue_ts[vid].append(int(r["date"]))
        except (ValueError, TypeError):
            pass
    _iv_uppers = [7, 14, 30, 90, 180, 365, 999999]
    _iv_labels  = ['≤1 week', '1–2 weeks', '2–4 weeks', '1–3 months', '3–6 months', '6–12 months', '> 1 year']
    _iv_counts  = [0] * len(_iv_uppers)
    for _ts_list in _venue_ts.values():
        if len(_ts_list) < 2:
            continue
        for _i in range(1, len(_ts_list)):
            _days = (_ts_list[_i] - _ts_list[_i - 1]) / 86400
            for _bi, _up in enumerate(_iv_uppers):
                if _days <= _up:
                    _iv_counts[_bi] += 1
                    break
    revisit_intervals = [[_iv_labels[i], _iv_counts[i]] for i in range(len(_iv_labels))]

    # ── Distance per year (haversine between consecutive check-ins) ────────────
    import math as _math
    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
        dphi = _math.radians(lat2 - lat1)
        dlam = _math.radians(lng2 - lng1)
        a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlam / 2) ** 2
        return R * 2 * _math.asin(_math.sqrt(a))

    _coord_rows: list[tuple[int, float, float]] = []
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
            if lat_f == 0.0 and lng_f == 0.0:
                continue
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _coord_rows.append((yr, lat_f, lng_f))
        except (ValueError, KeyError, TypeError, OSError):
            pass

    _dist_yr: dict[int, float] = defaultdict(float)
    for i in range(1, len(_coord_rows)):
        yr, lat2, lng2 = _coord_rows[i]
        _, lat1, lng1 = _coord_rows[i - 1]
        try:
            d = _haversine(lat1, lng1, lat2, lng2)
            if d < 20_000:  # filter GPS teleportation artifacts
                _dist_yr[yr] += d
        except Exception:
            pass
    dist_by_year: list[list] = sorted([[str(yr), round(v)] for yr, v in _dist_yr.items()])
    total_km = round(sum(_dist_yr.values()))

    # ── Streak tracker ────────────────────────────────────────────────────────
    from datetime import timedelta as _td
    _all_dates = sorted({d.date() for d in dates})
    longest_streak = 0
    current_streak = 0
    if _all_dates:
        streak = 1
        for i in range(1, len(_all_dates)):
            if (_all_dates[i] - _all_dates[i - 1]).days == 1:
                streak += 1
            else:
                longest_streak = max(longest_streak, streak)
                streak = 1
        longest_streak = max(longest_streak, streak)
        _today = datetime.now(tz=timezone.utc).date()
        if _all_dates[-1] >= _today - _td(days=1):
            current_streak = 1
            j = len(_all_dates) - 2
            while j >= 0 and (_all_dates[j + 1] - _all_dates[j]).days == 1:
                current_streak += 1
                j -= 1

    # ── New countries by year (first visit per country) ───────────────────────
    _first_country: dict[str, int] = {}
    _first_city: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        co = r.get("country", "").strip()
        ci = r.get("city", "").strip()
        if (co and co not in _first_country) or (ci and ci not in _first_city):
            try:
                _seen_yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            except (ValueError, OSError):
                continue
            if co and co not in _first_country:
                _first_country[co] = _seen_yr
            if ci and ci not in _first_city:
                _first_city[ci] = _seen_yr
    if new_country_year_overrides:
        for co, yr in new_country_year_overrides.items():
            if co in _first_country:
                _first_country[co] = yr
    _ncby: dict[str, list] = defaultdict(list)
    for co, yr in _first_country.items():
        _ncby[str(yr)].append(co)
    new_country_by_year = sorted([[yr, sorted(cos)] for yr, cos in _ncby.items()])

    # ── Countries per year ────────────────────────────────────────────────────
    _cpy: dict[int, set] = defaultdict(set)
    for r in rows:
        co = r.get("country", "").strip()
        if not co:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cpy[yr].add(co)
        except (ValueError, OSError):
            pass
    countries_per_year = sorted([[str(yr), len(cos)] for yr, cos in _cpy.items()])

    # ── Category drift (top 6 groups share by year) ───────────────────────────
    _cg_year: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        grp = categorize(cat)
        if not grp:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cg_year[yr][grp] += 1
        except (ValueError, OSError):
            pass
    _top_grps = [g for g, _ in cat_groups.most_common(7)]
    cat_drift = sorted([
        [str(yr), {g: ctr.get(g, 0) for g in _top_grps}]
        for yr, ctr in _cg_year.items()
    ])

    # ── Trips ─────────────────────────────────────────────────────────────────
    trips = detect_trips(rows, home_city=home_city, min_checkins=min_trip_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags)

    # ── Trip analytics (Group 3) ───────────────────────────────────────────────
    if trips:
        _HOME_LAT, _HOME_LNG = 53.9045, 27.5615  # Minsk

        # Duration histogram
        _dur_buckets = [1, 3, 7, 14, 28, 999]
        _dur_labels  = ['1 day', '2–3 days', '4–7 days', '1–2 weeks', '2–4 weeks', '4+ weeks']
        _dur_counts  = [0] * len(_dur_buckets)
        for _t in trips:
            _d = _t['duration']
            for _bi, _up in enumerate(_dur_buckets):
                if _d <= _up:
                    _dur_counts[_bi] += 1
                    break
        trip_duration_hist = [[_dur_labels[i], _dur_counts[i]] for i in range(len(_dur_labels))]

        # Countries per trip
        _cpt: dict[str, int] = {'1': 0, '2': 0, '3': 0, '4+': 0}
        for _t in trips:
            _n = len(_t['countries'])
            _k = str(_n) if _n <= 3 else '4+'
            _cpt[_k] = _cpt.get(_k, 0) + 1
        trip_countries_dist = [[k, _cpt[k]] for k in ['1', '2', '3', '4+']]

        # Top 10 longest trips
        _trips_by_dur = sorted(trips, key=lambda _t: -_t['duration'])[:10]
        trip_top_longest = [
            [_t['name'], _t['duration'], _t['countries'], _t['start_year']]
            for _t in _trips_by_dur
        ]

        # KPIs
        _avg_dur     = round(sum(_t['duration'] for _t in trips) / len(trips))
        _longest_t   = max(trips, key=lambda _t: _t['duration'])
        _most_cos_t  = max(trips, key=lambda _t: len(_t['countries']))
        _max_dist_km = 0.0
        _furthest: dict = {'km': 0, 'venue': '', 'city': '', 'country': ''}
        for _t in trips:
            for _ck in _t['checkins']:
                try:
                    _d = _haversine(_HOME_LAT, _HOME_LNG, float(_ck['lat']), float(_ck['lng']))
                    if _d > _max_dist_km:
                        _max_dist_km = _d
                        _furthest = {
                            'km':      round(_d),
                            'venue':   _ck['venue'],
                            'city':    _ck['city'],
                            'country': _ck['country'],
                        }
                except (TypeError, ValueError):
                    pass
        trip_kpis = {
            'avg_days':           _avg_dur,
            'longest_days':       _longest_t['duration'],
            'longest_name':       _longest_t['name'],
            'longest_year':       _longest_t['start_year'],
            'max_countries':      len(_most_cos_t['countries']),
            'max_countries_name': _most_cos_t['name'],
            'furthest_km':        _furthest['km'],
            'furthest_venue':     _furthest['venue'],
            'furthest_city':      _furthest['city'],
            'furthest_country':   _furthest['country'],
        }
    else:
        trip_duration_hist = []
        trip_countries_dist = []
        trip_top_longest = []
        trip_kpis = {}

    def _trip_km(t: dict) -> int:
        cks = sorted(t["checkins"], key=lambda c: c.get("date", 0))
        dist = 0.0
        for i in range(1, len(cks)):
            try:
                dist += _haversine(float(cks[i-1]["lat"]), float(cks[i-1]["lng"]),
                                   float(cks[i]["lat"]),   float(cks[i]["lng"]))
            except (TypeError, ValueError):
                pass
        return round(dist)

    timeline = [
        {
            "id":       t["id"],
            "name":     t["name"],
            "start":    t["start_date"],
            "end":      t["end_date"],
            "days":     t["duration"],
            "countries":t["countries"][:6],
            "count":    t["checkin_count"],
            "year":     t["start_year"],
            "km":       _trip_km(t),
        }
        for t in trips
    ]

    # ── Recent 30 ─────────────────────────────────────────────────────────────
    valid_rows = [r for r in rows if r.get("date", "").strip()]
    recent_sorted = sorted(valid_rows, key=lambda r: int(r["date"]), reverse=True)[:30]
    recent: list[dict] = []
    for r in recent_sorted:
        d = _parse_ts(r)
        if not d:
            continue
        try:
            lat = round(float(r["lat"]), 5)
        except (ValueError, KeyError, TypeError):
            lat = None
        try:
            lng = round(float(r["lng"]), 5)
        except (ValueError, KeyError, TypeError):
            lng = None
        # Localise the display timestamp to the check-in location's timezone.
        # Country lookup takes priority (handles DST-exempt countries like Belarus),
        # falling back to coordinate-based resolution.
        # tz_name is also passed to the Open-Meteo archive API for correct local hour.
        country_str = r.get("country", "").strip()
        tz_name = _COUNTRY_TZ.get(country_str) or _tz_at(lat, lng)
        d_local = _localise(d, lat, lng, country_str)
        recent.append(
            {
                "ts":       int(r["date"]),
                "date":     d_local.strftime("%Y-%m-%d"),
                "time":     d_local.strftime("%H:%M"),
                "datetime": d_local.strftime("%d %b %Y, %H:%M"),
                "venue":    r.get("venue",    "").strip(),
                "venue_id": r.get("venue_id", "").strip(),
                "city":     r.get("city",     "").strip(),
                "country":  r.get("country",  "").strip(),
                "category": r.get("category", "").strip(),
                "lat":        lat,
                "lng":        lng,
                "tz_name":    tz_name,
                "checkin_id": r.get("checkin_id", "").strip(),
                "companions": collect_companions(r),
            }
        )

    # ── Shout text mining + cross-dimensional analytics ──────────────────────
    shout_stats = shout_analysis(rows)
    cross_dim   = cross_dim_analysis(rows, categorize)

    # ── Tier 1.1 — Transport mode classification + walking + carbon ──────────
    # Speeds inferred from consecutive (ts, lat, lng) pairs.  Each segment is
    # classified into a mode; the chart shows km/year per mode + carbon est.
    # Coefficients (g CO2e per km): flight 285, car/bus 75, train 41, walk 0.
    # Boundaries below are conservative (drop 15-min gaps to avoid stop noise).
    _MODES = ["walking", "ground", "rail_likely", "flight"]
    _CO2 = {"walking": 0, "ground": 75, "rail_likely": 41, "flight": 285}
    _seg_rows = []
    for r in sorted(rows, key=lambda r: int(r.get("date", "0") or "0")):
        try:
            t = int(r["date"]); la = float(r["lat"]); lo = float(r["lng"])
            if la == 0 and lo == 0:
                continue
            _seg_rows.append((t, la, lo))
        except (ValueError, KeyError, TypeError):
            pass
    _mode_km_by_year: dict[int, dict[str, float]] = defaultdict(lambda: {m: 0.0 for m in _MODES})
    _co2_by_year: dict[int, float] = defaultdict(float)
    _flight_legs = []   # [year, from_lat, from_lng, to_lat, to_lng, km]
    _walk_km_total = 0.0
    for i in range(1, len(_seg_rows)):
        t0, la0, lo0 = _seg_rows[i - 1]
        t1, la1, lo1 = _seg_rows[i]
        dt = t1 - t0
        if dt <= 0 or dt > 86400:           # cap to 24h between pts
            continue
        try:
            d_km = _haversine(la0, lo0, la1, lo1)
        except Exception:
            continue
        if d_km <= 0 or d_km > 20000:
            continue
        # km/h
        speed = d_km / (dt / 3600.0)
        # mode buckets (defensive — middle band stays "ground")
        if speed > 200:
            mode = "flight"
        elif 80 < speed <= 200 and d_km > 50:
            mode = "rail_likely"
        elif speed <= 6 and d_km < 12:
            mode = "walking"
        else:
            mode = "ground"
        yr = datetime.fromtimestamp(t1, tz=timezone.utc).year
        _mode_km_by_year[yr][mode] += d_km
        _co2_by_year[yr] += d_km * _CO2[mode]
        if mode == "flight":
            _flight_legs.append([yr, round(la0, 3), round(lo0, 3),
                                  round(la1, 3), round(lo1, 3), round(d_km)])
        elif mode == "walking":
            _walk_km_total += d_km
    transport_modes = sorted([
        [str(yr), {m: round(_mode_km_by_year[yr][m]) for m in _MODES}]
        for yr in _mode_km_by_year
    ])
    transport_co2 = sorted([[str(yr), round(_co2_by_year[yr] / 1000)] for yr in _co2_by_year])
    transport_kpis = {
        "flight_legs":     len(_flight_legs),
        "walk_km_total":   round(_walk_km_total),
        "co2_tonnes":      round(sum(_co2_by_year.values()) / 1_000_000, 1),
    }
    # Limit flight legs payload to the longest 100 for the map overlay
    flight_legs = sorted(_flight_legs, key=lambda x: -x[5])[:100]

    # ── Tier 1.2 — Cohort venue retention ────────────────────────────────────
    # For each venue, get the year of FIRST visit (cohort) and every year visited.
    # cohort_retention[i][j] = % of cohort-i venues revisited in year-i+j.
    _venue_years: dict[str, set] = defaultdict(set)
    _venue_first: dict[str, int] = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
        except (ValueError, OSError):
            continue
        _venue_years[vid].add(yr)
        if vid not in _venue_first or yr < _venue_first[vid]:
            _venue_first[vid] = yr
    _all_years = sorted(set(_venue_first.values()))
    _cohort_size: dict[int, int] = Counter(_venue_first.values())
    cohort_retention = []
    for cohort_yr in _all_years:
        cohort_vids = [v for v, y in _venue_first.items() if y == cohort_yr]
        size = len(cohort_vids)
        _ret_by_year: list[int | None] = []
        for yr in _all_years:
            if yr < cohort_yr:
                _ret_by_year.append(None)
            else:
                hits = sum(1 for v in cohort_vids if yr in _venue_years[v])
                _ret_by_year.append(round(hits * 100 / size) if size else 0)
        cohort_retention.append({"cohort": cohort_yr, "size": size, "by_year": _ret_by_year})

    # ── Tier 1.3 — Distance from home + nomad score ──────────────────────────
    # Per-day mean distance from home centroid + rolling 30-day mean for chart.
    _HOME_LAT, _HOME_LNG = country_centroids.get(home_city, [53.9045, 27.5615, 0])[:2] \
        if home_city in country_centroids else (53.9045, 27.5615)
    # Try to use the actual home_city centroid first (more accurate than country)
    if home_city in city_centroids:
        _HOME_LAT, _HOME_LNG = city_centroids[home_city]
    _daily_dist: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        try:
            t = int(r["date"]); la = float(r["lat"]); lo = float(r["lng"])
            if la == 0 and lo == 0:
                continue
            d = _haversine(_HOME_LAT, _HOME_LNG, la, lo)
            key = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
            _daily_dist[key].append(d)
        except (ValueError, KeyError, TypeError, OSError):
            pass
    # Sample: one mean per day, sorted
    distance_from_home: list[list] = []
    for k in sorted(_daily_dist.keys()):
        m = sum(_daily_dist[k]) / len(_daily_dist[k])
        distance_from_home.append([k, round(m)])
    # Nomad score: % of days where mean > 50km
    _nomad_days = sum(1 for k, m in distance_from_home if m > 50)
    nomad_kpis = {
        "nomad_days":    _nomad_days,
        "total_days":    len(distance_from_home),
        "nomad_pct":     round(_nomad_days * 100 / len(distance_from_home), 1) if distance_from_home else 0,
        "max_dist_km":   round(max((m for _, m in distance_from_home), default=0)),
    }

    # ── Tier 1.4 — Daily extremes (first / last check-in of day drift) ───────
    # Per day in LOCAL time, find earliest and latest hour (decimal).
    # Then aggregate per year (median first/last) so the chart shows long-term drift.
    _day_extremes: dict[str, dict] = defaultdict(lambda: {"first": 24, "last": 0})
    for r in rows:
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        try:
            la = float(r["lat"]); lo = float(r["lng"])
        except (ValueError, KeyError, TypeError):
            la = lo = None
        d_utc = datetime.fromtimestamp(t, tz=timezone.utc)
        d_loc = _localise(d_utc, la, lo, r.get("country", "").strip())
        key = d_loc.strftime("%Y-%m-%d")
        h = d_loc.hour + d_loc.minute / 60.0
        if h < _day_extremes[key]["first"]:
            _day_extremes[key]["first"] = h
        if h > _day_extremes[key]["last"]:
            _day_extremes[key]["last"] = h
    # Aggregate to median per year
    _ext_by_year: dict[int, dict] = defaultdict(lambda: {"first": [], "last": []})
    for key, ext in _day_extremes.items():
        yr = int(key[:4])
        _ext_by_year[yr]["first"].append(ext["first"])
        _ext_by_year[yr]["last"].append(ext["last"])
    def _med(xs):
        if not xs:
            return None
        xs2 = sorted(xs)
        n = len(xs2)
        return xs2[n // 2] if n % 2 else (xs2[n // 2 - 1] + xs2[n // 2]) / 2
    daily_extremes = sorted([
        [str(yr),
         round(_med(_ext_by_year[yr]["first"]), 2),
         round(_med(_ext_by_year[yr]["last"]),  2)]
        for yr in _ext_by_year
    ])

    # ── Tier 2.1 — Companion lifecycle (first / last seen, gap, active span) ─
    # For each top-15 companion compute: first_ts, last_ts, active_days, n_meetings,
    # median gap (days), most-common city, top 3 venues.
    _comp_ts: dict[str, list] = defaultdict(list)        # name -> [ts, ...]
    _comp_venues: dict[str, Counter] = defaultdict(Counter)
    _comp_cities: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        names = set()
        for name in [n.strip() for n in r.get("with_name", "").replace(" ,", ",").split(",")]:
            if name:
                names.add(name)
        cb = r.get("created_by_name", "").strip()
        if cb:
            names.add(cb)
        for name in [n.strip() for n in r.get("overlaps_name", "").replace(" ,", ",").split(",") if n.strip() and n.strip() != "-"]:
            names.add(name)
        venue = r.get("venue", "").strip()
        city  = r.get("city",  "").strip()
        for n in names:
            _comp_ts[n].append(t)
            if venue:
                _comp_venues[n][venue] += 1
            if city:
                _comp_cities[n][city] += 1
    companion_lifecycle = []
    for name, _cnt in comp_raw.most_common(20):
        ts_list = sorted(_comp_ts[name])
        if not ts_list:
            continue
        first_ts = ts_list[0]
        last_ts  = ts_list[-1]
        gaps = [(ts_list[i] - ts_list[i - 1]) / 86400 for i in range(1, len(ts_list))]
        med_gap = _med(gaps) if gaps else 0
        top_v = _comp_venues[name].most_common(1)
        top_c = _comp_cities[name].most_common(1)
        companion_lifecycle.append({
            "name":      name,
            "n":         len(ts_list),
            "first_ts":  first_ts,
            "last_ts":   last_ts,
            "active_days": round((last_ts - first_ts) / 86400),
            "med_gap":   round(med_gap, 1) if med_gap else 0,
            "top_venue": top_v[0][0] if top_v else "",
            "top_city":  top_c[0][0] if top_c else "",
        })

    # ── Tier 2.3 — City graduation funnel ────────────────────────────────────
    _funnel_thresholds = [1, 5, 10, 25, 50, 100, 250]
    _city_visit_counts = cities  # already a Counter
    _funnel_counts = []
    for t in _funnel_thresholds:
        _funnel_counts.append(sum(1 for _, n in _city_visit_counts.items() if n >= t))
    city_funnel = [[t, n] for t, n in zip(_funnel_thresholds, _funnel_counts)]

    # ── Tier 2.4 — Activity decay per category (absolute counts per year) ────
    _cat_abs: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cat = r.get("category", "").strip()
        if not cat:
            continue
        grp = categorize(cat)
        if not grp:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _cat_abs[grp][yr] += 1
        except (ValueError, OSError):
            pass
    _years_sorted = sorted({yr for yrs in _cat_abs.values() for yr in yrs})
    cat_trajectory_abs = []
    for grp in _top_grps:
        series = [_cat_abs[grp].get(yr, 0) for yr in _years_sorted]
        cat_trajectory_abs.append({"group": grp, "series": series})
    cat_trajectory_years = [str(yr) for yr in _years_sorted]

    # ── Tier 5.2 — source_app trends ─────────────────────────────────────────
    _app_by_year: dict[int, Counter] = defaultdict(Counter)
    for r in rows:
        app = (r.get("source_app", "") or "").strip()
        if not app:
            continue
        try:
            yr = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc).year
            _app_by_year[yr][app] += 1
        except (ValueError, OSError):
            pass
    _all_apps = set()
    for c in _app_by_year.values():
        _all_apps |= set(c.keys())
    _top_apps = sorted(_all_apps,
                       key=lambda a: -sum(_app_by_year[y].get(a, 0) for y in _app_by_year))[:6]
    source_app_by_year = sorted([
        [str(yr), {a: _app_by_year[yr].get(a, 0) for a in _top_apps}]
        for yr in _app_by_year
    ])

    # ── Tier 6.4 — Diversity index (Shannon entropy of category mix / year) ─
    diversity_by_year = []
    import math as _math2
    for yr, ctr in _cg_year.items():
        total = sum(ctr.values())
        if not total:
            continue
        h = 0.0
        for v in ctr.values():
            p = v / total
            if p > 0:
                h -= p * _math2.log2(p)
        diversity_by_year.append([str(yr), round(h, 2)])
    diversity_by_year.sort()

    # ── Tier 6.3 — Dormant venues ("what you miss") ──────────────────────────
    # Venues with >=10 historical visits but no visit in the last 365 days,
    # surfaced as top 30 by total visits.
    _365_ago = (int(datetime.now(tz=timezone.utc).timestamp())) - 365 * 86400
    _v_last: dict[str, int] = {}
    _v_total: Counter = Counter()
    _v_info: dict[str, tuple] = {}
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            t = int(r["date"])
        except (ValueError, KeyError):
            continue
        if t > _v_last.get(vid, 0):
            _v_last[vid] = t
        _v_total[vid] += 1
        if vid not in _v_info:
            _v_info[vid] = (r.get("venue", "").strip(), r.get("city", "").strip())
    dormant_venues = []
    for vid, last in _v_last.items():
        if _v_total[vid] >= 10 and last < _365_ago:
            nm, cy = _v_info[vid]
            dormant_venues.append({
                "name":    nm,
                "city":    cy,
                "visits":  _v_total[vid],
                "last_ts": last,
                "days_ago": round((int(datetime.now(tz=timezone.utc).timestamp()) - last) / 86400),
            })
    dormant_venues.sort(key=lambda x: -x["visits"])
    dormant_venues = dormant_venues[:30]

    # ── Tier 6.1 — Year-in-review with vivid narrative ──────────────────────
    # For each year compute a small set of headline facts and a short narrative.
    # Each year_summary also feeds the per-year album page (gen_year_pages.py).
    _months_full = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"]
    _MONTH_VIBE = {
        "January":   "deep-winter",
        "February":  "snow-bound",
        "March":     "thawing",
        "April":     "spring-edged",
        "May":       "blooming",
        "June":      "long-day",
        "July":      "midsummer",
        "August":    "late-summer",
        "September": "amber",
        "October":   "autumnal",
        "November":  "grey-skied",
        "December":  "year-closing",
    }
    # Compute year totals first so we can compare each year to the personal record
    _yr_totals = {yr: 0 for yr in {d.year for d in dates}}
    _yr_rows: dict[int, list] = defaultdict(list)
    for r in rows:
        try:
            t = int(r.get("date", 0) or 0)
        except ValueError:
            continue
        if not t:
            continue
        try:
            yr = datetime.fromtimestamp(t, tz=timezone.utc).year
        except OSError:
            continue
        _yr_rows[yr].append(r)
        _yr_totals[yr] = _yr_totals.get(yr, 0) + 1
    _max_yr_total = max(_yr_totals.values()) if _yr_totals else 0
    _mean_yr_total = sum(_yr_totals.values()) / len(_yr_totals) if _yr_totals else 0

    # ── Magazine-prose phrasing pools ──────────────────────────────────────
    # Each pool is picked deterministically by year so adjacent years feel
    # different but every rebuild reads the same.  Goal: literary, atmospheric
    # sentences that name places and patterns rather than dump counts.
    _BUSY_INTROS = [
        "A year that <strong>widened its arc</strong> beyond all the others",
        "Your <strong>widest-open year</strong> on record",
        "Twelve months that moved <strong>almost without pause</strong>",
        "The busiest stretch you've ever logged",
    ]
    _TRAVEL_INTROS = [
        "A year of <strong>long horizons</strong>",
        "Borders bent often, and somewhere new came into focus",
        "A heavy-travelling year, threaded with departures",
        "Twelve months that kept turning the map",
    ]
    _ROAM_INTROS = [
        "A year of <strong>far reach</strong> — many flags, many time-zones",
        "The map stretched wide this year",
        "A passport-stamped year, full of new horizons",
        "A wandering year, country after country",
    ]
    _HOME_INTROS = [
        "A year <strong>closer to home</strong> than usual",
        "The year you stayed put — and noticed more",
        "A grounded year, the kind familiar places deepen",
        "Less distance, more familiarity",
    ]
    _QUIET_INTROS = [
        "A <strong>softer</strong> year by the count",
        "Fewer stops, more between them",
        "A year of restraint — pauses, second visits, slow streets",
        "A quieter chapter than the years around it",
    ]
    _DEFAULT_INTROS = [
        "A steady year through the world",
        "Twelve months of measured going and returning",
        "A balanced year — familiar grounds and a few new ones",
        "A year that found its own rhythm",
    ]
    _ANCHOR_PHRASES = [
        "with <strong>{city}</strong> as the gravitational centre",
        "anchored deep in <strong>{city}</strong>",
        "always returning to <strong>{city}</strong>",
        "with <strong>{city}</strong> pulling the thread back home",
        "orbiting <strong>{city}</strong> between everything else",
    ]
    _PEAK_PHRASES = [
        "and {vibe}<strong>{mon}</strong> burning the brightest stretch",
        "with {vibe}<strong>{mon}</strong> as the loudest weeks",
        "peaking in the {vibe}weeks of <strong>{mon}</strong>",
        "with {vibe}<strong>{mon}</strong> running fastest of all",
    ]
    _NEW_PHRASES = [
        "<strong>{n} fresh flags</strong> appeared on the map",
        "you added <strong>{n}</strong> first-time countries to the rotation",
        "<strong>{n}</strong> new horizons opened",
        "<strong>{n}</strong> new countries entered the album",
    ]
    _ANCHOR_VENUE = [
        "and <strong>{v}</strong> quietly became a daily ritual",
        "and <strong>{v}</strong> turned into your steady fixture",
        "while you orbited <strong>{v}</strong> again and again",
    ]
    _COMPANION_PHRASES = [
        "and much of it travelled with <strong>{c}</strong>",
        "with <strong>{c}</strong> as the steady companion through it",
        "more often than not, in the company of <strong>{c}</strong>",
    ]
    _FIRST_COUNTRY_PHRASES = [
        "<strong>{c}</strong> entered the album for the first time",
        "you set foot in <strong>{c}</strong> for the first time",
        "<strong>{c}</strong> opened up as a first-time country",
    ]
    _JOURNEY_ONE = [
        "One journey broke the routine — <strong>{name}</strong>, setting out in {mon}",
        "A single expedition left town: <strong>{name}</strong>, in {mon}",
        "The one real departure was <strong>{name}</strong>, come {mon}",
    ]
    _JOURNEY_FEW = [
        "{n} journeys shaped it — {list}",
        "{n} trips punctuated the calendar: {list}",
        "The road called {n} times — {list}",
    ]
    _JOURNEY_MANY = [
        "{n} journeys threaded the year, opening with <strong>{first}</strong> in {fm} and closing with <strong>{last}</strong> in {lm}",
        "{n} trips in all, from <strong>{first}</strong> in {fm} to <strong>{last}</strong> in {lm}",
        "The suitcase barely rested — {n} departures, bookended by <strong>{first}</strong> ({fm}) and <strong>{last}</strong> ({lm})",
    ]
    _FAR_PHRASES = [
        "reaching as far as <strong>{city}</strong>, {km} km from home",
        "at its farthest touching <strong>{city}</strong> — {km} km out",
        "stretching all the way to <strong>{city}</strong>, {km} km from home",
    ]
    _DIST_PHRASES = [
        "Some <strong>{km} km</strong> passed under you between check-ins",
        "<strong>{km} km</strong> logged on the move",
        "It added up to <strong>{km} km</strong> of ground covered",
    ]
    _NEW_CITY_PHRASES = [
        "<strong>{n}</strong> cities seen for the first time — {sample} among them",
        "<strong>{n}</strong> first-time cities joined the map, {sample} included",
        "the album gained <strong>{n}</strong> new cities, {sample} among them",
    ]
    _OPEN_CLOSE = [
        "The year opened at <strong>{fv}</strong>{fc} and signed off in {lm} at <strong>{lv}</strong>{lc}",
        "It began at <strong>{fv}</strong>{fc} in {fm} and ended at <strong>{lv}</strong>{lc}",
        "First entry: <strong>{fv}</strong>{fc}, {fm}; the last word went to <strong>{lv}</strong>{lc}",
    ]

    def _pick(pool: list[str], yr: int, salt: int = 0) -> str:
        """Deterministic pick — same year reads the same on every build."""
        return pool[(yr + salt) % len(pool)]

    def _cap_html(s: str) -> str:
        """Capitalize the first plain letter, skipping any HTML opener."""
        i = 0
        while i < len(s) and s[i] == "<":
            close = s.find(">", i)
            if close < 0:
                break
            i = close + 1
        return s[:i] + s[i].upper() + s[i + 1:] if i < len(s) else s

    def _vivid(yr: int, total: int, peak_mon_name: str, peak_mon_n: int,
               top_city: str, top_city_n: int, top_cat: str, top_cat_n: int,
               top_venue: str, top_venue_n: int, n_new_countries: int,
               n_cities: int, n_countries: int, top_companion: str = "",
               first_new_country: str = "", trips_y: list | None = None,
               distance_km: int = 0, n_new_cities: int = 0,
               new_city_sample: list[str] | None = None,
               first_stop: tuple[str, str, str] = ("", "", ""),
               last_stop: tuple[str, str, str] = ("", "", ""),
               farthest_city: str = "", farthest_km: int = 0) -> str:
        """Compose a magazine-style year storyline (3-5 short sentences).

        S1 — the year's character + place anchor + temporal peak.
        S2 — the journeys: trips taken, farthest reach, distance covered.
        S3 — discovery: first-time countries and cities.
        S4 — the end-to-end arc (first and last check-in of the year) plus
             a flourish (anchor venue or companion).
        Every phrase is picked deterministically by year so each rebuild
        reads the same, but adjacent years read differently.
        """
        # Pick the intro by year character
        if total >= 0.95 * _max_yr_total:
            intro = _pick(_BUSY_INTROS, yr)
        elif total >= 0.7 * _max_yr_total:
            intro = _pick(_TRAVEL_INTROS, yr)
        elif n_countries >= 15:
            intro = _pick(_ROAM_INTROS, yr)
        elif n_countries <= 3 and total > 0.4 * _max_yr_total:
            intro = _pick(_HOME_INTROS, yr)
        elif total < 0.3 * _mean_yr_total:
            intro = _pick(_QUIET_INTROS, yr)
        else:
            intro = _pick(_DEFAULT_INTROS, yr)

        # ── S1 — character + anchor + peak ─────────────────────────────
        s1_parts: list[str] = [intro]
        if top_city and top_city_n and top_city_n > max(40, total * 0.10):
            s1_parts.append(_pick(_ANCHOR_PHRASES, yr, 1).format(city=top_city))
        if peak_mon_name:
            vibe = _MONTH_VIBE.get(peak_mon_name, "")
            vibe_str = (vibe + " ") if vibe else ""
            s1_parts.append(_pick(_PEAK_PHRASES, yr, 2).format(vibe=vibe_str, mon=peak_mon_name))
        sentence1 = ", ".join(s1_parts).rstrip(",") + "."

        def _trip_mon(t: dict) -> str:
            try:
                return _months_full[int(str(t.get("start_date", ""))[5:7]) - 1]
            except (ValueError, IndexError):
                return ""

        # ── S2 — the journeys ──────────────────────────────────────────
        s2_bits: list[str] = []
        tl = trips_y or []
        if len(tl) == 1:
            s2_bits.append(_pick(_JOURNEY_ONE, yr, 6).format(
                name=tl[0].get("name") or "a trip", mon=_trip_mon(tl[0])))
        elif 2 <= len(tl) <= 3:
            listed = ", ".join(
                f"<strong>{t.get('name') or 'a trip'}</strong> in {_trip_mon(t)}"
                for t in tl)
            s2_bits.append(_pick(_JOURNEY_FEW, yr, 6).format(n=len(tl), list=listed))
        elif len(tl) >= 4:
            s2_bits.append(_pick(_JOURNEY_MANY, yr, 6).format(
                n=len(tl),
                first=tl[0].get("name") or "a trip", fm=_trip_mon(tl[0]),
                last=tl[-1].get("name") or "a trip", lm=_trip_mon(tl[-1])))
        if farthest_city and farthest_km >= 500 and farthest_city != top_city:
            s2_bits.append(_pick(_FAR_PHRASES, yr, 7).format(
                city=farthest_city, km=f"{farthest_km:,}"))
        sentence2 = ""
        if s2_bits:
            sentence2 = _cap_html(", ".join(s2_bits)) + "."
        elif distance_km >= 2000:
            sentence2 = _pick(_DIST_PHRASES, yr, 7).format(km=f"{distance_km:,}") + "."

        # ── S3 — discovery (first-time countries and cities) ───────────
        s3_bits: list[str] = []
        if n_new_countries:
            if n_new_countries <= 2 and first_new_country:
                s3_bits.append(_pick(_FIRST_COUNTRY_PHRASES, yr, 3).format(c=first_new_country))
            else:
                s3_bits.append(_pick(_NEW_PHRASES, yr, 3).format(n=n_new_countries))
        if n_new_cities >= 4 and new_city_sample:
            sample = " and ".join(f"<strong>{c}</strong>" for c in new_city_sample[:2])
            s3_bits.append(_pick(_NEW_CITY_PHRASES, yr, 8).format(n=n_new_cities, sample=sample))
        sentence3 = _cap_html(", ".join(s3_bits[:2])) + "." if s3_bits else ""

        # ── S4 — end-to-end arc + flourish ─────────────────────────────
        s4_bits: list[str] = []
        fv, fc, fm = first_stop
        lv, lc, lm = last_stop
        if fv and lv and fv != lv:
            fc_str = f" in {fc}" if fc and fc != top_city else ""
            lc_str = f" in {lc}" if lc and lc != top_city else ""
            s4_bits.append(_pick(_OPEN_CLOSE, yr, 9).format(
                fv=fv, fc=fc_str, fm=fm, lv=lv, lc=lc_str, lm=lm))
        if top_venue and top_venue_n >= 35 and top_venue not in (fv, lv):
            s4_bits.append(_pick(_ANCHOR_VENUE, yr, 4).format(v=top_venue))
        if top_companion:
            s4_bits.append(_pick(_COMPANION_PHRASES, yr, 5).format(c=top_companion))
        joined4 = ", ".join(s4_bits[:2])
        if joined4.startswith("and "):
            joined4 = joined4[4:]
        sentence4 = _cap_html(joined4) + "." if joined4 else ""

        return " ".join(
            s for s in [sentence1, sentence2, sentence3, sentence4] if s
        ).strip()

    year_summaries = []
    for yr in sorted({d.year for d in dates}):
        rows_y = _yr_rows.get(yr, [])
        if not rows_y:
            continue
        cat_y = Counter()
        ven_y = Counter()
        cty_y = Counter()
        cou_y = Counter()
        mon_y = Counter()
        comp_y: Counter = Counter()
        for r in rows_y:
            try:
                d = datetime.fromtimestamp(int(r["date"]), tz=timezone.utc)
            except (ValueError, OSError):
                continue
            mon_y[d.month] += 1
            if r.get("category"):
                cat_y[r["category"]] += 1
            if r.get("venue_id"):
                ven_y[(r["venue_id"], r.get("venue", ""))] += 1
            if r.get("city"):
                cty_y[r["city"]] += 1
            if r.get("country"):
                cou_y[r["country"]] += 1
            for n in collect_companions(r):
                comp_y[n] += 1
        peak_mon = mon_y.most_common(1)[0] if mon_y else (0, 0)
        top_v = ven_y.most_common(1)[0] if ven_y else ((None, ""), 0)
        top_c = cat_y.most_common(1)[0] if cat_y else ("", 0)
        top_city = cty_y.most_common(1)[0] if cty_y else ("", 0)
        peak_month_name = _months_full[peak_mon[0] - 1] if peak_mon[0] else ""
        n_new_countries = len([c for c, y in _first_country.items() if y == yr])
        n_distance_y = next((int(v) for k, v in dist_by_year if k == str(yr)), 0)
        trips_y = [t for t in trips if t.get("start_year") == yr]
        n_trips_y = len(trips_y)
        new_countries_list = sorted([c for c, y in _first_country.items() if y == yr])
        # First-time cities this year, sampled by how much they mattered
        new_cities_set = {c for c, y in _first_city.items() if y == yr}
        new_city_sample = [c for c, _ in cty_y.most_common()
                           if c in new_cities_set and c != top_city[0]][:2]
        # Farthest reach from home + the year's opening/closing check-ins
        far_city, far_km = "", 0.0
        for r in rows_y:
            try:
                la, ln = float(r["lat"]), float(r["lng"])
            except (ValueError, KeyError, TypeError):
                continue
            if la == 0.0 and ln == 0.0:
                continue
            d_km = _haversine(_HOME_LAT, _HOME_LNG, la, ln)
            if d_km > far_km:
                far_km = d_km
                far_city = r.get("city", "").strip() or r.get("country", "").strip()

        def _stop(r: dict) -> tuple[str, str, str]:
            try:
                mon = _months_full[datetime.fromtimestamp(
                    int(r["date"]), tz=timezone.utc).month - 1]
            except (ValueError, OSError):
                mon = ""
            return (r.get("venue", "").strip(), r.get("city", "").strip(), mon)

        rows_y_sorted = sorted(rows_y, key=lambda r: int(r.get("date", "0") or "0"))
        vivid = _vivid(
            yr=yr, total=len(rows_y),
            peak_mon_name=peak_month_name, peak_mon_n=peak_mon[1],
            top_city=top_city[0], top_city_n=top_city[1],
            top_cat=top_c[0], top_cat_n=top_c[1],
            top_venue=top_v[0][1] if top_v[0] else "", top_venue_n=top_v[1] if top_v else 0,
            n_new_countries=n_new_countries,
            n_cities=len(cty_y), n_countries=len(cou_y),
            top_companion=comp_y.most_common(1)[0][0] if comp_y else "",
            first_new_country=new_countries_list[0] if new_countries_list else "",
            trips_y=trips_y,
            distance_km=n_distance_y,
            n_new_cities=len(new_cities_set),
            new_city_sample=new_city_sample,
            first_stop=_stop(rows_y_sorted[0]) if rows_y_sorted else ("", "", ""),
            last_stop=_stop(rows_y_sorted[-1]) if rows_y_sorted else ("", "", ""),
            farthest_city=far_city, farthest_km=round(far_km),
        )
        year_summaries.append({
            "year":              yr,
            "total":             len(rows_y),
            "peak_month":        peak_month_name[:3] if peak_month_name else "",
            "peak_month_full":   peak_month_name,
            "peak_month_n":      peak_mon[1],
            "top_venue":         top_v[0][1] if top_v[0] else "",
            "top_venue_id":      top_v[0][0] if top_v[0] else "",
            "top_venue_n":       top_v[1] if top_v else 0,
            "top_cat":           top_c[0],
            "top_cat_n":         top_c[1],
            "top_city":          top_city[0],
            "top_city_n":        top_city[1],
            "new_countries":     n_new_countries,
            "new_countries_list": new_countries_list,
            "cities":            len(cty_y),
            "countries":         len(cou_y),
            "vivid":             vivid,
            "distance_km":       n_distance_y,
            "trip_count":        n_trips_y,
            "top_companion":     comp_y.most_common(1)[0][0] if comp_y else "",
            "new_cities":        len(new_cities_set),
            "farthest_city":     far_city,
            "farthest_km":       round(far_km),
        })

    # ── Tier 3.1 — city_inferred KPI ─────────────────────────────────────────
    inferred_n = sum(1 for r in rows if str(r.get("city_inferred", "0")) == "1")
    city_inferred_kpis = {
        "inferred_n":    inferred_n,
        "total":         len(rows),
        "inferred_pct":  round(inferred_n * 100 / len(rows), 1) if rows else 0,
    }

    log.info("Cities: %d | Countries: %d | Unique places: %d | Trips: %d",
             len(cities), len(countries), unique_count, len(trips))
    if shout_stats:
        log.info("Shouts: %d analyzed | %d words | %d emoji types",
                 shout_stats.get("total_shouts", 0),
                 shout_stats.get("total_words", 0),
                 len(shout_stats.get("top_emojis", [])))

    if not dates:
        raise ValueError("No valid date rows found in CSV.")

    stats: dict = {
        "total":              len(rows),
        "date_min":           str(min(dates).date()),
        "date_max":           str(max(dates).date()),
        "unique_places_count":unique_count,
        "by_year":            sorted([(str(k), v) for k, v in by_year.items()]),
        "by_month":           sorted([(f"{k[0]}-{k[1]:02d}", v) for k, v in by_month.items()]),
        "by_hour":            [(k, v) for k, v in sorted(by_hour.items())],
        "by_dow":             [(k, v) for k, v in sorted(by_dow.items())],
        "countries":          [[c, n] for c, n in countries.most_common()],
        "countries_by_venues":countries_by_venues,
        "cities":             [[c, n, city_primary_country.get(c, "")] for c, n in cities.most_common()],
        "cities_by_venues":   cities_by_venues,
        "city_centroids":     city_centroids,
        "country_centroids":  country_centroids,
        "venues":             venues_list,
        "cat_groups":         cat_groups.most_common(),
        "explorer_cats":      explorer_cats,
        "explorer_groups":    explorer_groups,
        "explorer":           explorer,
        "unique_places":      unique_places,
        "all_coords":         all_coords,
        "venues_heatmap":     venues_heatmap,
        "venues_by_year":     venues_by_year,
        "venues_by_catgrp":   venues_by_catgrp,
        "companions":         companions,
        "solo_vs_group_by_year": solo_vs_group_by_year,
        "solo_vs_group_totals":  solo_vs_group_totals,
        "companion_countries":   companion_countries,
        "heatmap":            heatmap,
        "discovery_rate":     discovery_rate,
        "venue_loyalty":      venue_loyalty,
        "timeline":           timeline,
        "trips_count":        len(trips),
        "recent":             recent,
        "venue_freq_dist":    venue_freq_dist,
        "venue_regulars":     venue_regulars,
        "revisit_intervals":  revisit_intervals,
        "dist_by_year":       dist_by_year,
        "total_km":           total_km,
        "longest_streak":     longest_streak,
        "current_streak":     current_streak,
        "new_country_by_year":new_country_by_year,
        "countries_per_year": countries_per_year,
        "cat_drift":          cat_drift,
        "cat_drift_groups":   _top_grps,
        "trip_duration_hist": trip_duration_hist,
        "trip_countries_dist":trip_countries_dist,
        "trip_top_longest":   trip_top_longest,
        "trip_kpis":          trip_kpis,
        "shout_stats":        shout_stats,
        "cross_dim":          cross_dim,
        # ── New tier-1/2/3/5/6 analytics ──
        "transport_modes":    transport_modes,
        "transport_co2":      transport_co2,
        "transport_kpis":     transport_kpis,
        "flight_legs":        flight_legs,
        "cohort_retention":   cohort_retention,
        "distance_from_home": distance_from_home,
        "nomad_kpis":         nomad_kpis,
        "daily_extremes":     daily_extremes,
        "companion_lifecycle":companion_lifecycle,
        "city_funnel":        city_funnel,
        "cat_trajectory_abs": cat_trajectory_abs,
        "cat_trajectory_years": cat_trajectory_years,
        "source_app_by_year": source_app_by_year,
        "diversity_by_year":  diversity_by_year,
        "dormant_venues":     dormant_venues,
        "year_summaries":     year_summaries,
        "city_inferred_kpis": city_inferred_kpis,
    }
    return stats, trips
