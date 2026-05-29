# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""flights.py — Parse a FlightRadar24 flight-diary CSV export and:

  1. Normalise it into a clean list of flight records with IATA codes,
     parsed durations, and km estimates.
  2. Match each flight to the user's "Plane" / "Airport" check-ins by
     date proximity, returning a checkin_id → flight info map.
  3. Compute aggregate stats: total flights, hours, km, top airlines,
     top aircraft, top routes, flights per year.

Exposed surface (called from build.py):
    load_flights(path)        → list[dict]
    build_iata_coords(rows, flights) → {iata: (lat, lng, name)}
    match_to_checkins(flights, rows, iata_coords) → {cid: {…}}
    summarise(flights, iata_coords) → dict (stats payload)

FR24 export shape (one row per leg):
    Date, Flight number, From, To, Dep time, Arr time, Duration,
    Airline, Aircraft, Registration, Seat number, Seat type, …

`From` / `To` cells embed both IATA and ICAO codes, e.g.
    "Chisinau / Chisinau Airport (RMO/LUKK)"
so we parse out the IATA prefix.  When the FR24 codes look wrong
(e.g. RMO is what FR24 uses for Chișinău but IATA is KIV) we keep
the FR24 spelling — it's the user's authoritative label.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


_CODE_PAREN = re.compile(r"\(([A-Z0-9]{3})/([A-Z0-9]{4})\)\s*$")


def _parse_iata(cell: str) -> tuple[str, str, str]:
    """Return (iata, icao, name) from a 'Chisinau / Chisinau Airport (RMO/LUKK)' cell.

    Falls back gracefully when codes are missing.
    """
    if not cell:
        return ("", "", "")
    cell = cell.strip()
    m = _CODE_PAREN.search(cell)
    if not m:
        return ("", "", cell)
    iata, icao = m.group(1), m.group(2)
    name = cell[: m.start()].rstrip(" /")
    # Some cells repeat "Foo / Foo Airport" — keep the longer/right half.
    parts = [p.strip() for p in name.split("/")]
    if len(parts) == 2 and parts[1].lower().endswith("airport"):
        name = parts[1]
    elif len(parts) == 2:
        name = parts[1] or parts[0]
    return (iata, icao, name)


def _parse_duration_min(cell: str) -> int:
    """Parse 'HH:MM:SS' or 'HH:MM' → minutes."""
    if not cell:
        return 0
    parts = cell.strip().split(":")
    try:
        if len(parts) == 3:
            h, m, _ = parts
            return int(h) * 60 + int(m)
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return 0
    return 0


def _haversine_km(la1: float, lo1: float, la2: float, lo2: float) -> float:
    R = 6371.0
    r = math.pi / 180
    dla = (la2 - la1) * r
    dlo = (lo2 - lo1) * r
    a = (math.sin(dla / 2) ** 2
         + math.cos(la1 * r) * math.cos(la2 * r) * math.sin(dlo / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def load_flights(path: Path | str) -> list[dict]:
    """Read an FR24 flight-diary CSV.  Skips any leading blank lines that
    FR24 sometimes prepends to the file."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    # FR24 sometimes prepends a blank line; strip it before parsing.
    raw = p.read_text(encoding="utf-8-sig").lstrip("\r\n")
    rdr = csv.DictReader(raw.splitlines())
    for i, row in enumerate(rdr):
        date = (row.get("Date") or "").strip()
        if not date:
            continue
        from_iata, from_icao, from_name = _parse_iata(row.get("From") or "")
        to_iata,   to_icao,   to_name   = _parse_iata(row.get("To")   or "")
        dur_min = _parse_duration_min(row.get("Duration") or "")
        # Airline code: try "Belavia (B2/BRU)" → ("Belavia", "B2", "BRU")
        airline_cell = (row.get("Airline") or "").strip()
        airline_name = airline_cell
        airline_iata = ""
        m_air = re.search(r"\(([A-Z0-9]{1,3})/([A-Z0-9]{3})\)\s*$", airline_cell)
        if m_air:
            airline_iata = m_air.group(1)
            airline_name = airline_cell[: m_air.start()].rstrip(" /").strip()
        # Aircraft: "Airbus A320 (A320)" — capture the ICAO type at the end
        ac_cell = (row.get("Aircraft") or "").strip()
        ac_name = ac_cell
        ac_code = ""
        m_ac = re.search(r"\(([A-Z0-9]{3,4})\)\s*$", ac_cell)
        if m_ac:
            ac_code = m_ac.group(1)
            ac_name = ac_cell[: m_ac.start()].rstrip().strip()
        out.append({
            "idx":          i,
            "date":         date,
            "flight":       (row.get("Flight number") or "").strip(),
            "dep_time":     (row.get("Dep time") or "").strip(),
            "arr_time":     (row.get("Arr time") or "").strip(),
            "dur_min":      dur_min,
            "from_iata":    from_iata,
            "from_icao":    from_icao,
            "from_name":    from_name,
            "to_iata":      to_iata,
            "to_icao":      to_icao,
            "to_name":      to_name,
            "airline":      airline_name,
            "airline_iata": airline_iata,
            "aircraft":     ac_name,
            "aircraft_code": ac_code,
            "reg":          (row.get("Registration") or "").strip(),
            "seat":         (row.get("Seat number") or "").strip(),
            "note":         (row.get("Note") or "").strip(),
        })
    out.sort(key=lambda f: f["date"])
    return out


def build_iata_coords(checkin_rows: list[dict], flights: list[dict]) -> dict[str, tuple[float, float, str]]:
    """Build a {iata: (lat, lng, sample_name)} map.

    Strategy:
      1. For every Foursquare Airport-family check-in, try to detect the
         airport's IATA code (3-letter caps after a "(" anywhere in venue
         name, or appearing as a token in the venue).  If found, record
         the venue's lat/lng.
      2. For each flight in the diary, also probe by name match (FR24
         airport name → most-frequent matching Foursquare venue).

    The result is best-effort.  Unknown IATA codes simply don't get coords;
    the consumer must handle missing.
    """
    AIRPORT_CATS = {
        "Airport", "International Airport", "Airport Terminal",
        "Airport Gate", "Plane", "Travel Lounge", "Airport Lounge",
        "Travel and Transportation",
    }
    paren_iata_re = re.compile(r"\(([A-Z]{3})\)|^([A-Z]{3})\b")
    iata_coords: dict[str, list[tuple[float, float, str]]] = defaultdict(list)

    for r in checkin_rows:
        cat = (r.get("category") or "").strip()
        if cat not in AIRPORT_CATS:
            continue
        venue = (r.get("venue") or "").strip()
        if not venue:
            continue
        try:
            la = float(r.get("lat") or 0)
            lo = float(r.get("lng") or 0)
        except (ValueError, TypeError):
            continue
        if (la, lo) == (0.0, 0.0):
            continue
        m = paren_iata_re.search(venue)
        if m:
            iata = (m.group(1) or m.group(2) or "").upper()
            if iata:
                iata_coords[iata].append((la, lo, venue))

    # Reduce: pick the centroid (mean) of all hits per IATA, store one name
    out: dict[str, tuple[float, float, str]] = {}
    for iata, samples in iata_coords.items():
        avg_la = sum(s[0] for s in samples) / len(samples)
        avg_lo = sum(s[1] for s in samples) / len(samples)
        # Pick the most-common name
        ctr = Counter(s[2] for s in samples)
        out[iata] = (round(avg_la, 5), round(avg_lo, 5), ctr.most_common(1)[0][0])

    # Sanity: also try a tiny manual lookup for the most common gaps in
    # FR24's reverse-IATA mappings (they sometimes use historical codes).
    # Use only when iata not already known via the check-in scan.
    HIST_FALLBACK = {
        # FR24 historical → standard, with rough coords
        "RMO": (46.9277, 28.9310, "Chișinău International Airport"),
        "KBP": (50.3450, 30.8946, "Kyiv Boryspil"),
        "DME": (55.4087, 37.9061, "Moscow Domodedovo"),
    }
    for iata, t in HIST_FALLBACK.items():
        out.setdefault(iata, t)
    return out


def match_to_checkins(flights: list[dict],
                      checkin_rows: list[dict],
                      iata_coords: dict[str, tuple[float, float, str]]
                      ) -> dict[str, dict]:
    """Return {checkin_id: {flight_info}}.

    Matching rules (in priority order):
      1. Plane / Airport check-ins on the SAME calendar date as a flight
         get linked to that flight.
      2. Multiple eligible check-ins per flight → all link to the same
         flight record (so departure + gate + plane all show the same).
      3. A given check-in only links to ONE flight (the nearest in time).
    """
    AIRPORT_CATS = {
        "Airport", "International Airport", "Airport Terminal",
        "Airport Gate", "Plane", "Travel Lounge", "Airport Lounge",
    }

    # Bucket flights by date for O(1) lookup
    flights_by_date: dict[str, list[dict]] = defaultdict(list)
    for f in flights:
        flights_by_date[f["date"]].append(f)

    matched: dict[str, dict] = {}
    for r in checkin_rows:
        cat = (r.get("category") or "").strip()
        if cat not in AIRPORT_CATS:
            continue
        cid = (r.get("checkin_id") or "").strip()
        if not cid:
            continue
        try:
            ts = int(r.get("date") or 0)
        except ValueError:
            continue
        if not ts:
            continue
        d_utc = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        # Also probe ±1 day for late-night arrivals straddling UTC midnight
        candidates: list[dict] = []
        for probe in (d_utc,
                      (datetime.fromtimestamp(ts - 86400, tz=timezone.utc).strftime("%Y-%m-%d")),
                      (datetime.fromtimestamp(ts + 86400, tz=timezone.utc).strftime("%Y-%m-%d"))):
            candidates.extend(flights_by_date.get(probe, []))
        if not candidates:
            continue
        # Pick closest in time to the dep_time
        best = None
        best_delta = 99999
        for f in candidates:
            try:
                dt_str = f["date"] + " " + (f["dep_time"] or "00:00:00")
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                delta = abs((dt.timestamp() - ts)) / 3600  # hours
            except Exception:
                delta = 24
            if delta < best_delta:
                best_delta = delta
                best = f
        if not best:
            continue
        # Build the flight info payload for display
        dep_ll = iata_coords.get(best["from_iata"])
        arr_ll = iata_coords.get(best["to_iata"])
        km = ""
        if dep_ll and arr_ll:
            km = round(_haversine_km(dep_ll[0], dep_ll[1], arr_ll[0], arr_ll[1]))
        matched[cid] = {
            "flight":      best["flight"],
            "airline":     best["airline"],
            "airline_iata": best["airline_iata"],
            "aircraft":    best["aircraft_code"] or best["aircraft"],
            "reg":         best["reg"],
            "seat":        best["seat"],
            "from_iata":   best["from_iata"],
            "to_iata":     best["to_iata"],
            "from_name":   best["from_name"],
            "to_name":     best["to_name"],
            "date":        best["date"],
            "dep_time":    best["dep_time"],
            "arr_time":    best["arr_time"],
            "dur_min":     best["dur_min"],
            "km":          km,
        }
    return matched


def summarise(flights: list[dict],
              iata_coords: dict[str, tuple[float, float, str]]) -> dict:
    """Aggregate stats payload for the Flight History card."""
    if not flights:
        return {}
    total_min = sum(f["dur_min"] for f in flights)
    total_km = 0
    by_year: dict[int, dict] = defaultdict(lambda: {"n": 0, "km": 0, "min": 0})
    airlines: Counter = Counter()
    aircraft: Counter = Counter()
    routes: Counter = Counter()           # ordered route ("MSQ→IST")
    routes_undirected: Counter = Counter() # collapsed ("MSQ↔IST")
    airports: Counter = Counter()
    legs: list[dict] = []                 # for map polylines

    for f in flights:
        try:
            yr = int(f["date"][:4])
        except (ValueError, IndexError):
            continue
        km = 0
        dep_ll = iata_coords.get(f["from_iata"])
        arr_ll = iata_coords.get(f["to_iata"])
        if dep_ll and arr_ll:
            km = round(_haversine_km(dep_ll[0], dep_ll[1], arr_ll[0], arr_ll[1]))
            legs.append({
                "year": yr,
                "from": f["from_iata"], "to": f["to_iata"],
                "from_ll": [dep_ll[0], dep_ll[1]],
                "to_ll":   [arr_ll[0], arr_ll[1]],
                "km": km,
            })
        total_km += km
        by_year[yr]["n"]   += 1
        by_year[yr]["km"]  += km
        by_year[yr]["min"] += f["dur_min"]
        if f["airline"]:
            airlines[f["airline"]] += 1
        if f["aircraft_code"]:
            aircraft[f["aircraft_code"]] += 1
        if f["from_iata"] and f["to_iata"]:
            routes[f"{f['from_iata']}→{f['to_iata']}"] += 1
            u = tuple(sorted([f["from_iata"], f["to_iata"]]))
            routes_undirected["↔".join(u)] += 1
        if f["from_iata"]:
            airports[f["from_iata"]] += 1
        if f["to_iata"]:
            airports[f["to_iata"]] += 1

    return {
        "total_flights":    len(flights),
        "total_hours":      round(total_min / 60, 1),
        "total_km":         total_km,
        "by_year":          sorted([[str(yr), v["n"], v["km"], round(v["min"] / 60, 1)]
                                    for yr, v in by_year.items()]),
        "top_airlines":     airlines.most_common(8),
        "top_aircraft":     aircraft.most_common(8),
        "top_routes":       routes.most_common(15),
        "top_routes_pair":  routes_undirected.most_common(10),
        "top_airports":     airports.most_common(15),
        "legs":             legs,
    }
