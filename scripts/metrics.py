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

    extended: list[list[dict]] = []
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
    raw_trips = extended

    result: list[dict] = []
    for trip_rows, _resolved_tags, _name_ts in raw_trips:
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
                    "ts":       int(r["date"]),
                    "date":     d_local.strftime("%Y-%m-%d"),
                    "time":     d_local.strftime("%H:%M"),
                    "datetime": d_local.strftime("%d %b %Y, %H:%M"),
                    "venue":    r.get("venue", "").strip(),
                    "venue_id": r.get("venue_id", "").strip(),
                    "city":     r.get("city", "").strip(),
                    "country":  r.get("country", "").strip(),
                    "category": r.get("category", "").strip(),
                    "lat":      lat,
                    "lng":      lng,
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
                "coords":         [[c["lat"], c["lng"]] for c in checkins if c["lat"] and c["lng"]],
                "unique_pts":     unique_pts,
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

    # ── Unique places ─────────────────────────────────────────────────────────
    seen_ids: set[str] = set()
    seen_coords: set[tuple] = set()
    unique_places: list = []
    for r in rows:
        vid = r.get("venue_id", "").strip()
        try:
            lat, lng = round(float(r["lat"]), 5), round(float(r["lng"]), 5)
            has_coords = True
        except (ValueError, KeyError, TypeError):
            has_coords = False

        if vid:
            if vid not in seen_ids:
                seen_ids.add(vid)
                if has_coords:
                    unique_places.append([lat, lng, r.get("venue", "").strip()])
        elif has_coords:
            key = (lat, lng)
            if key not in seen_coords:
                seen_coords.add(key)
                unique_places.append([lat, lng, r.get("venue", "").strip()])

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
    countries_by_venues = sorted(
        [[c, len(v)] for c, v in country_vids.items()], key=lambda x: -x[1]
    )
    cities_by_venues = sorted(
        [[cy, len(v), city_primary_country.get(cy, "")] for cy, v in city_vids.items()],
        key=lambda x: -x[1]
    )

    # ── All coords (kept for dot map) ────────────────────────────────────────
    all_coords: list = []
    for r in rows:
        try:
            all_coords.append([round(float(r["lat"]), 5), round(float(r["lng"]), 5)])
        except (ValueError, KeyError, TypeError):
            pass

    # ── Venues heatmap: one point per ~111m cell, weight = log(visit count) ──
    # Grouping at 3dp merges GPS micro-jitter; log dampens Minsk dominance.
    import math as _math
    _vh: dict = {}  # venue_id → (lat, lng, count)
    for r in rows:
        vid = r.get("venue_id", "").strip()
        if not vid:
            continue
        try:
            lat_f, lng_f = float(r["lat"]), float(r["lng"])
        except (ValueError, KeyError, TypeError):
            continue
        if vid not in _vh:
            _vh[vid] = [lat_f, lng_f, 0]
        _vh[vid][2] += 1
    _vh_max = _math.log1p(max(v[2] for v in _vh.values())) if _vh else 1.0
    venues_heatmap: list = [
        [v[0], v[1], round(_math.log1p(v[2]) / _vh_max, 4)]
        for v in _vh.values()
    ]

    # ── Companions ────────────────────────────────────────────────────────────
    comp_raw: Counter = Counter()
    for r in rows:
        raw = r.get("with_name", "").strip()
        if not raw:
            continue
        for name in [n.strip() for n in raw.replace(" ,", ",").split(",")]:
            if name:
                comp_raw[name] += 1
    companions = [[n, c] for n, c in comp_raw.most_common(30)]

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

    # ── Trips ─────────────────────────────────────────────────────────────────
    trips = detect_trips(rows, home_city=home_city, min_checkins=min_trip_checkins, trip_names=trip_names, trip_exclude=trip_exclude, trip_end_overrides=trip_end_overrides, trip_start_overrides=trip_start_overrides, trip_tags=trip_tags)
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
                "lat":      lat,
                "lng":      lng,
                "tz_name":  tz_name,
            }
        )

    log.info("Cities: %d | Countries: %d | Unique places: %d | Trips: %d",
             len(cities), len(countries), unique_count, len(trips))

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
        "explorer":           explorer,
        "unique_places":      unique_places,
        "all_coords":         all_coords,
        "venues_heatmap":     venues_heatmap,
        "companions":         companions,
        "heatmap":            heatmap,
        "discovery_rate":     discovery_rate,
        "venue_loyalty":      venue_loyalty,
        "timeline":           timeline,
        "trips_count":        len(trips),
        "recent":             recent,
    }
    return stats, trips
