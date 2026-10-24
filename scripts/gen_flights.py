#!/usr/bin/env python3
# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""gen_flights.py — Generate flights.html, the dedicated dashboard for
every flight in your FlightRadar24 export.

Page content:
  • Hero: total flights / hours / km / unique airports
  • World map with great-circle polylines (Leaflet)
  • Year-by-year strip with bar chart
  • Filters: year, airline, aircraft
  • Cards (one per flight): airline logo, route, flight number,
    aircraft + reg, duration, distance, date
"""

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path


def _hav_km(la1: float, lo1: float, la2: float, lo2: float) -> int:
    """Great-circle distance between two lat/lon points, km."""
    R = 6371.0
    r = math.pi / 180
    d_la = (la2 - la1) * r
    d_lo = (lo2 - lo1) * r
    a = (math.sin(d_la / 2) ** 2
         + math.cos(la1 * r) * math.cos(la2 * r) * math.sin(d_lo / 2) ** 2)
    return round(R * 2 * math.asin(math.sqrt(a)))


def _summarise(flights: list[dict], iata_coords: dict) -> dict:
    """Crunch a flights list into derived stats. Returns a dict that
    drives the KPI strip, records, scrollable top lists, yearly
    breakdown, tail-number list, busiest single-day records, etc."""
    n_total = len(flights)
    airlines: Counter = Counter()
    aircraft_types: Counter = Counter()
    airports: Counter = Counter()
    routes_dir: Counter = Counter()      # directed (A→B ≠ B→A)
    routes_pair: Counter = Counter()     # undirected
    regs: Counter = Counter()            # tail numbers
    years: Counter = Counter()
    dow: Counter = Counter()
    durs: list[int] = []
    legs: list[tuple[int, dict]] = []    # (km, flight)
    by_date: Counter = Counter()         # flights per date string
    by_year_km: Counter = Counter()
    by_year_min: Counter = Counter()
    notes_count = 0

    for f in flights:
        if f.get("airline"):
            airlines[f["airline"]] += 1
        ac = f.get("aircraft_code") or f.get("aircraft") or ""
        if ac:
            aircraft_types[ac] += 1
        reg = (f.get("reg") or "").strip()
        if reg:
            regs[reg] += 1
        if (f.get("note") or "").strip():
            notes_count += 1
        fa, ta = f.get("from_iata", ""), f.get("to_iata", "")
        if fa: airports[fa] += 1
        if ta: airports[ta] += 1
        leg_km = 0
        if fa and ta:
            routes_dir[(fa, ta)] += 1
            routes_pair[tuple(sorted([fa, ta]))] += 1
            ca, cb = iata_coords.get(fa), iata_coords.get(ta)
            if ca and cb and ca[0] and cb[0]:
                leg_km = _hav_km(ca[0], ca[1], cb[0], cb[1])
                legs.append((leg_km, f))
        dur = f.get("dur_min") or 0
        if dur > 0:
            durs.append(dur)
        try:
            d = datetime.strptime(f.get("date", "")[:10], "%Y-%m-%d")
            years[d.year] += 1
            dow[d.weekday()] += 1   # Monday = 0
            by_date[f.get("date", "")[:10]] += 1
            if leg_km:
                by_year_km[d.year] += leg_km
            if dur:
                by_year_min[d.year] += dur
        except (ValueError, TypeError):
            pass

    # Same-day connections — count flights that share a date and where the
    # arrival of one is the departure of the next (loose definition).
    by_date_flights: dict[str, list[dict]] = {}
    for f in flights:
        d = f.get("date", "")[:10]
        if d:
            by_date_flights.setdefault(d, []).append(f)
    same_day_conn = 0
    for d, fs in by_date_flights.items():
        # If 2+ flights on the same date, count the connecting pairs
        if len(fs) >= 2:
            same_day_conn += len(fs) - 1

    legs.sort(key=lambda x: -x[0])
    longest = legs[0] if legs else None
    shortest = legs[-1] if legs else None
    busiest_yr = years.most_common(1)[0] if years else (None, 0)
    avg_dur = round(sum(durs) / len(durs)) if durs else 0
    longest_dur = max(durs) if durs else 0
    busiest_day = by_date.most_common(1)[0] if by_date else (None, 0)

    # First / latest flight (by date string)
    flights_dated = [f for f in flights if f.get("date")]
    flights_dated.sort(key=lambda f: f["date"])
    first_flight = flights_dated[0] if flights_dated else None
    latest_flight = flights_dated[-1] if flights_dated else None

    # Approx CO2: passenger aviation ≈ 0.115 kg CO2e per km (economy)
    total_km = sum(by_year_km.values())
    co2_kg = round(total_km * 0.115)

    return {
        "n_total": n_total,
        "n_airlines": len(airlines),
        "n_aircraft_types": len(aircraft_types),
        "n_airports": len(airports),
        "n_routes_dir": len(routes_dir),
        "n_routes_pair": len(routes_pair),
        "n_regs": len(regs),
        "n_notes": notes_count,
        "n_same_day_conn": same_day_conn,
        "co2_kg": co2_kg,
        "avg_dur_min": avg_dur,
        "longest_dur_min": longest_dur,
        "busiest_year": busiest_yr,
        "busiest_day": busiest_day,
        "first_flight": first_flight,
        "latest_flight": latest_flight,
        "longest": {
            "km": longest[0],
            "from": longest[1].get("from_iata", ""),
            "to": longest[1].get("to_iata", ""),
            "date": longest[1].get("date", ""),
            "dur_min": longest[1].get("dur_min", 0),
            "airline": longest[1].get("airline", ""),
        } if longest else None,
        "shortest": {
            "km": shortest[0],
            "from": shortest[1].get("from_iata", ""),
            "to": shortest[1].get("to_iata", ""),
            "date": shortest[1].get("date", ""),
            "dur_min": shortest[1].get("dur_min", 0),
            "airline": shortest[1].get("airline", ""),
        } if shortest else None,
        # Full sorted lists for scrollable display
        "all_airlines": airlines.most_common(),
        "all_aircraft": aircraft_types.most_common(),
        "all_routes": routes_pair.most_common(),
        "all_airports": airports.most_common(),
        "all_regs": regs.most_common(),
        "busiest_days": by_date.most_common(10),
        "dow": [dow.get(i, 0) for i in range(7)],
        "years": [
            (y, n, by_year_km.get(y, 0), by_year_min.get(y, 0))
            for y, n in sorted(years.items())
        ],
    }


def build_page(csv_path, config_dir, out_path, tmpl_path=None,
               flights_data=None, flight_history=None, iata_coords=None,
               **_extra) -> None:
    flights_data = flights_data or []
    flight_history = flight_history or {}
    iata_coords = iata_coords or {}

    out_path = Path(out_path)
    if not flights_data:
        out_path.write_text(
            "<!doctype html><meta charset=utf-8><title>Flights</title>"
            "<style>body{background:#0c0e14;color:#7a85a8;font-family:sans-serif;"
            "padding:48px;text-align:center;}</style>"
            "<h1>No flights data</h1>"
            "<p>Drop a FlightRadar24 export at <code>data/flights.csv</code> and rebuild.</p>",
            encoding="utf-8",
        )
        print(f"flights.html -> {out_path}  (empty)")
        return

    # Inline iata_coords map as JSON for client-side route rendering
    iata_for_js = {k: [v[0], v[1], v[2]] for k, v in iata_coords.items()}

    # Slim flights payload for client
    slim = [{
        "date":         f.get("date", ""),
        "flight":       f.get("flight", ""),
        "from_iata":    f.get("from_iata", ""),
        "to_iata":      f.get("to_iata", ""),
        "from_name":    f.get("from_name", ""),
        "to_name":      f.get("to_name", ""),
        "airline":      f.get("airline", ""),
        "airline_iata": f.get("airline_iata", ""),
        "aircraft":     f.get("aircraft", ""),
        "aircraft_code": f.get("aircraft_code", ""),
        "reg":          f.get("reg", ""),
        "dur_min":      f.get("dur_min", 0),
        "seat":         f.get("seat", ""),
        "note":         f.get("note", ""),
    } for f in flights_data]
    slim.sort(key=lambda x: x["date"], reverse=True)

    flights_json = json.dumps(slim, ensure_ascii=False).replace("</", "<\\/")
    coords_json = json.dumps(iata_for_js, ensure_ascii=False).replace("</", "<\\/")
    history_json = json.dumps(flight_history, ensure_ascii=False).replace("</", "<\\/")

    # Derive richer stats
    stats = _summarise(flights_data, iata_coords)

    # Build the Records HTML server-side so it's there pre-JS
    def _fmt_dur(m):
        return f"{m // 60}h {m % 60:02d}m" if m else "—"

    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    longest = stats["longest"]
    shortest = stats["shortest"]
    busy_y, busy_n = stats["busiest_year"]

    records_html = ""
    if longest:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Longest leg</div>'
            f'<div class="rec-val">{_esc(longest["from"])} → {_esc(longest["to"])}</div>'
            f'<div class="rec-sub">{longest["km"]:,} km · {_fmt_dur(longest["dur_min"])} · {_esc(longest["date"])}</div>'
            f'<div class="rec-tag">{_esc(longest["airline"])}</div></div>'
        )
    if shortest and shortest["km"] > 0:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Shortest leg</div>'
            f'<div class="rec-val">{_esc(shortest["from"])} → {_esc(shortest["to"])}</div>'
            f'<div class="rec-sub">{shortest["km"]:,} km · {_fmt_dur(shortest["dur_min"])} · {_esc(shortest["date"])}</div>'
            f'<div class="rec-tag">{_esc(shortest["airline"])}</div></div>'
        )
    if busy_y:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Busiest year</div>'
            f'<div class="rec-val">{busy_y}</div>'
            f'<div class="rec-sub">{busy_n} flights</div></div>'
        )
    if stats["avg_dur_min"]:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Average duration</div>'
            f'<div class="rec-val">{_fmt_dur(stats["avg_dur_min"])}</div>'
            f'<div class="rec-sub">across {stats["n_total"]} legs</div></div>'
        )
    if stats["longest_dur_min"]:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Longest by time</div>'
            f'<div class="rec-val">{_fmt_dur(stats["longest_dur_min"])}</div>'
            f'<div class="rec-sub">single leg</div></div>'
        )
    if stats["busiest_day"][0]:
        d, n = stats["busiest_day"]
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Busiest single day</div>'
            f'<div class="rec-val">{n} flights</div>'
            f'<div class="rec-sub">{_esc(d)}</div></div>'
        )
    if stats["first_flight"]:
        ff = stats["first_flight"]
        records_html += (
            f'<div class="rec"><div class="rec-lbl">First flight on record</div>'
            f'<div class="rec-val">{_esc(ff.get("from_iata",""))} → {_esc(ff.get("to_iata",""))}</div>'
            f'<div class="rec-sub">{_esc(ff.get("date",""))} · {_esc(ff.get("airline",""))}</div></div>'
        )
    if stats["co2_kg"]:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">CO₂ footprint</div>'
            f'<div class="rec-val">~{stats["co2_kg"]:,} kg</div>'
            f'<div class="rec-sub">at 0.115 kg / passenger-km</div></div>'
        )
    if stats["n_same_day_conn"]:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Same-day connections</div>'
            f'<div class="rec-val">{stats["n_same_day_conn"]}</div>'
            f'<div class="rec-sub">layover legs</div></div>'
        )
    if stats["n_regs"]:
        records_html += (
            f'<div class="rec"><div class="rec-lbl">Distinct airframes</div>'
            f'<div class="rec-val">{stats["n_regs"]}</div>'
            f'<div class="rec-sub">tail numbers seen</div></div>'
        )

    # Full scrollable top lists rendered server-side
    def _list_block(label, items, fmt=None, count_label="×"):
        if not items:
            return ""
        lines = []
        for i, (name, n) in enumerate(items, 1):
            if fmt == "route":
                name = " ↔ ".join(name)
            lines.append(
                f'<div class="ti-row"><span class="ti-rank">#{i}</span>'
                f'<span class="ti-name">{_esc(name)}</span>'
                f'<span class="ti-n">{n}{count_label}</span></div>'
            )
        return (
            f'<div class="ti-block">'
            f'<div class="ti-lbl">{label} <span class="ti-total">· {len(items)}</span></div>'
            f'<div class="ti-list ti-scroll">{"".join(lines)}</div>'
            f'</div>'
        )

    top_block_html = (
        _list_block("All airlines", stats["all_airlines"])
        + _list_block("All routes", stats["all_routes"], fmt="route")
        + _list_block("All airports", stats["all_airports"])
        + _list_block("All aircraft", stats["all_aircraft"])
    )

    # Tail numbers (registrations) block
    tail_block_html = _list_block("Tail numbers", stats["all_regs"]) if stats["all_regs"] else ""

    # Busiest days list
    busy_days_html = ""
    if stats["busiest_days"]:
        rows_html = "".join(
            f'<div class="ti-row"><span class="ti-rank">#{i}</span>'
            f'<span class="ti-name">{_esc(d)}</span>'
            f'<span class="ti-n">{n} flights</span></div>'
            for i, (d, n) in enumerate(stats["busiest_days"], 1)
        )
        busy_days_html = (
            f'<div class="ti-block">'
            f'<div class="ti-lbl">Busiest days <span class="ti-total">· top {len(stats["busiest_days"])}</span></div>'
            f'<div class="ti-list ti-scroll">{rows_html}</div></div>'
        )

    # Yearly breakdown table
    yearly_html = ""
    if stats["years"]:
        max_n = max(n for _, n, *_ in stats["years"]) or 1
        rows_html = []
        for y, n, km, mins in stats["years"]:
            pct = round(100 * n / max_n)
            hours = round(mins / 60, 1) if mins else 0
            rows_html.append(
                f'<div class="yr-row">'
                f'<div class="yr-yr">{y}</div>'
                f'<div class="yr-bar-wrap"><div class="yr-bar" style="width:{pct}%"></div></div>'
                f'<div class="yr-n">{n}</div>'
                f'<div class="yr-km">{km:,} km</div>'
                f'<div class="yr-h">{hours} h</div>'
                f'</div>'
            )
        yearly_html = "".join(rows_html)

    # DOW mini-bar chart (Mon..Sun)
    dow_labels = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    dow = stats["dow"]
    dow_max = max(dow) or 1
    dow_html = ""
    for i, label in enumerate(dow_labels):
        v = dow[i]
        h = round(100 * v / dow_max) if v else 4
        dow_html += (
            f'<div class="dow-col" title="{label}: {v} flights">'
            f'<div class="dow-bar" style="height:{h}%"></div>'
            f'<div class="dow-num">{v}</div>'
            f'<div class="dow-lbl">{label}</div></div>'
        )

    html = HTML.format(
        flights_json=flights_json,
        coords_json=coords_json,
        history_json=history_json,
        total_flights=flight_history.get("total_flights", len(slim)),
        total_hours=flight_history.get("total_hours", 0),
        total_km=f"{flight_history.get('total_km', 0):,}",
        n_airlines=stats["n_airlines"],
        n_aircraft=stats["n_aircraft_types"],
        n_routes=stats["n_routes_pair"],
        n_airports=stats["n_airports"],
        n_regs=stats["n_regs"],
        co2_kg=f"{stats['co2_kg']:,}",
        same_day_conn=stats["n_same_day_conn"],
        records_html=records_html,
        top_block_html=top_block_html,
        tail_block_html=tail_block_html,
        busy_days_html=busy_days_html,
        yearly_html=yearly_html,
        dow_html=dow_html,
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"flights.html -> {out_path}  ({len(slim)} flights, {len(html)//1024} KB)")


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flights — Check-in Journal</title>
<meta name="description" content="Every flight you've taken, with routes, airlines, aircraft, and a world map of all great-circle paths.">
<link rel="canonical" href="https://4sq.pages.dev/flights.html">
<meta property="og:type" content="website">
<meta property="og:title" content="Flights — Check-in Journal">
<meta property="og:description" content="Every flight you've taken, with routes, airlines, aircraft, and a world map.">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://content.airhex.com" crossorigin>
<link rel="preconnect" href="https://unpkg.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:wght@400;500;600&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;}}
body.light{{--bg:#e9e4dc;--card:#f0ece3;--card2:#e5e0d7;--border:#aaa49a;--gold:#7a3e00;--teal:#155c58;--muted:#54576a;--text:#15172a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.5;}}
a{{color:var(--teal);}}

.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}

/* ── Hero: title, then the route map as the centrepiece ── */
.hero{{padding:64px 28px 10px;max-width:1300px;margin:0 auto;position:relative;}}
.hero .eyebrow{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--gold);letter-spacing:.22em;text-transform:uppercase;}}
.hero h1{{font-family:'Playfair Display',serif;font-size:clamp(2.3rem,5.6vw,4.4rem);font-weight:900;line-height:1;letter-spacing:-0.02em;margin:10px 0 10px;color:var(--text);}}
.hero h1 em{{font-style:normal;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.hero .sub{{color:var(--muted);font-size:.92rem;max-width:640px;}}
.map-section{{padding:18px 28px 6px;max-width:1300px;margin:0 auto;}}
#flMap{{width:100%;height:540px;border-radius:24px;background:#0c0e14;border:1px solid var(--border);}}
.leaflet-container{{background:#0c0e14;}}
.leaflet-popup-content-wrapper{{background:var(--card2);border:1px solid var(--border);color:var(--text);font-family:'DM Sans',sans-serif;font-size:.78rem;}}
.leaflet-popup-tip{{background:var(--card2);}}
.ap-lbl{{background:none;border:none;box-shadow:none;color:#eef0f6;font-family:'DM Mono',monospace;font-size:10px;padding:0;}}
.ap-lbl::before{{display:none;}}
/* ── Facts ── */
.facts{{padding:16px 28px 0;max-width:1300px;margin:0 auto;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;}}
.box{{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:18px 20px;min-width:0;}}
.box h3{{font-family:'DM Mono',monospace;font-weight:400;font-size:.56rem;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:12px;}}
.s2{{grid-column:span 2;}}.s4{{grid-column:span 4;}}.s6{{grid-column:span 6;}}
.huge{{font-family:'Playfair Display',serif;font-weight:900;font-size:2.6rem;line-height:1;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.huge.sea{{background:linear-gradient(135deg,#4cb8ff,#9b7bff);-webkit-background-clip:text;background-clip:text;}}
.huge.mint{{background:linear-gradient(135deg,#2fd6b5,#4cb8ff);-webkit-background-clip:text;background-clip:text;}}
.fact-sub{{color:var(--text);opacity:.8;font-size:.8rem;margin-top:8px;}}
.box.grow{{display:flex;flex-direction:column;}}
.fy{{display:flex;align-items:flex-end;gap:8px;flex:1;min-height:150px;}}
.fy-col{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:5px;min-width:0;height:100%;}}
.fy-col .t{{flex:1;width:100%;display:flex;align-items:flex-end;justify-content:center;}}
.fy-col i{{display:block;width:100%;max-width:40px;border-radius:8px 8px 3px 3px;background:linear-gradient(180deg,#f6d38b,#ff9a6b 60%,#ff6fae);}}
.fy-col .v,.fy-col .l{{font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);}}
.rt{{display:flex;align-items:center;gap:10px;padding:6px 0;font-family:'Playfair Display',serif;font-size:1.12rem;color:var(--text);}}
.rt+.rt{{border-top:1px solid var(--border);}}
.rt span{{color:var(--muted);font-size:.8rem;}}
.rt em{{margin-left:auto;font-family:'DM Mono',monospace;font-size:.66rem;font-style:normal;color:var(--teal);}}
.al-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:0 30px;}}
.al{{display:grid;grid-template-columns:32px minmax(0,1fr) 90px 28px;gap:10px;align-items:center;padding:5px 0;}}
.al .lg{{width:32px;height:32px;border-radius:9px;background:rgba(255,255,255,.94);padding:3px;object-fit:contain;}}
.al .lgfb{{width:32px;height:32px;border-radius:9px;background:rgba(232,184,109,.15);display:none;align-items:center;justify-content:center;color:var(--gold);}}
.al .nm{{font-size:.84rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.al .bar{{height:6px;border-radius:3px;background:var(--card2);overflow:hidden;}}
.al .bar i{{display:block;height:100%;background:linear-gradient(90deg,#4cb8ff,#9b7bff);}}
.al b{{font-family:'DM Mono',monospace;font-weight:400;font-size:.7rem;color:var(--gold);text-align:right;}}
@media(max-width:900px){{.facts{{grid-template-columns:repeat(2,minmax(0,1fr));}}.s2,.s4,.s6{{grid-column:span 2;}}}}

/* ── Records strip ── */
.records-section{{padding:30px 28px 0;max-width:1300px;margin:0 auto;}}
.records-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.22em;color:var(--gold);margin-bottom:8px;}}
.records-title{{font-family:'Playfair Display',serif;font-size:clamp(1.4rem,2.6vw,2.2rem);font-weight:700;color:var(--text);margin-bottom:20px;letter-spacing:-0.01em;}}
.records-title em{{font-style:normal;color:var(--gold);font-weight:500;}}
.records-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;}}
.rec{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;position:relative;border-left:3px solid var(--teal);transition:border-left-color .2s,transform .15s;}}
.rec:hover{{border-left-color:var(--gold);transform:translateY(-2px);}}
.rec-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.18em;color:var(--muted);margin-bottom:6px;}}
.rec-val{{font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:700;color:var(--gold);line-height:1.1;letter-spacing:-.01em;}}
.rec-sub{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--text);opacity:.75;margin-top:6px;}}
.rec-tag{{font-family:'DM Mono',monospace;font-size:.55rem;color:var(--teal);margin-top:4px;letter-spacing:.04em;}}

/* ── Top blocks ── */
.tops-section{{padding:30px 28px 0;max-width:1300px;margin:0 auto;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;}}
.ti-block{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;}}
.ti-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:12px;display:flex;align-items:baseline;justify-content:space-between;gap:8px;}}
.ti-total{{color:var(--muted);font-size:.92em;letter-spacing:.08em;}}
.ti-list{{display:flex;flex-direction:column;gap:5px;}}
/* Scrollable variant — caps height, custom scrollbar styling */
.ti-scroll{{max-height:280px;overflow-y:auto;padding-right:6px;scrollbar-width:thin;scrollbar-color:rgba(232,184,109,.4) transparent;}}
.ti-scroll::-webkit-scrollbar{{width:6px;}}
.ti-scroll::-webkit-scrollbar-track{{background:transparent;}}
.ti-scroll::-webkit-scrollbar-thumb{{background:rgba(232,184,109,.35);border-radius:3px;}}
.ti-scroll::-webkit-scrollbar-thumb:hover{{background:rgba(232,184,109,.55);}}
.ti-row{{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);}}
.ti-row:last-child{{border-bottom:none;}}
.ti-rank{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);width:22px;text-align:right;flex-shrink:0;}}
.ti-name{{flex:1;font-size:.76rem;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.ti-n{{font-family:'DM Mono',monospace;font-size:.68rem;color:var(--teal);flex-shrink:0;}}

/* ── Yearly breakdown ── */
.yearly-section{{padding:30px 28px 0;max-width:1300px;margin:0 auto;}}
.yearly-wrap{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 22px;}}
.yearly-head{{display:grid;grid-template-columns:60px 1fr 70px 110px 70px;gap:14px;align-items:baseline;font-family:'DM Mono',monospace;font-size:.5rem;text-transform:uppercase;letter-spacing:.18em;color:var(--muted);padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:6px;}}
.yr-row{{display:grid;grid-template-columns:60px 1fr 70px 110px 70px;gap:14px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.03);}}
.yr-row:last-child{{border-bottom:none;}}
.yr-yr{{font-family:'Playfair Display',serif;font-size:1.05rem;font-weight:700;color:var(--gold);}}
.yr-bar-wrap{{height:8px;background:rgba(255,255,255,.04);border-radius:4px;overflow:hidden;}}
.yr-bar{{height:100%;background:linear-gradient(90deg,#f5d48a,#e8b86d 60%,#b97c30);box-shadow:0 0 8px rgba(232,184,109,.25);border-radius:4px;transition:width .4s;}}
.yr-n{{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--text);font-weight:600;text-align:right;}}
.yr-km{{font-family:'DM Mono',monospace;font-size:.66rem;color:var(--text);text-align:right;opacity:.85;}}
.yr-h{{font-family:'DM Mono',monospace;font-size:.66rem;color:var(--teal);text-align:right;}}
@media(max-width:600px){{.yearly-head,.yr-row{{grid-template-columns:42px 1fr 50px 80px 50px;gap:8px;}}}}

/* ── Day-of-week chart ── */
.dow-section{{padding:30px 28px 16px;max-width:1300px;margin:0 auto;}}
.dow-chart{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 18px 14px;display:grid;grid-template-columns:repeat(7,1fr);gap:10px;height:200px;align-items:end;}}
.dow-col{{display:flex;flex-direction:column;align-items:center;gap:6px;height:100%;justify-content:flex-end;cursor:default;}}
.dow-bar{{width:100%;max-width:36px;background:linear-gradient(180deg,#f5d48a 0%,#e8b86d 60%,#b97c30);border-radius:6px 6px 2px 2px;min-height:4px;transition:filter .15s;box-shadow:0 0 10px rgba(232,184,109,.18);}}
.dow-col:hover .dow-bar{{filter:brightness(1.2);}}
.dow-num{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--text);font-weight:600;}}
.dow-lbl{{font-family:'DM Mono',monospace;font-size:.5rem;color:var(--muted);letter-spacing:.1em;}}

/* ── Filter bar ── */
.fbar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:18px 28px;background:var(--card);border-bottom:1px solid var(--border);max-width:1400px;margin:0 auto;border-radius:14px;margin-top:16px;}}
.fbar select,.fbar input{{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:6px 12px;color:var(--text);font-size:.78rem;font-family:'DM Sans',sans-serif;outline:none;cursor:pointer;}}
.fbar select:focus,.fbar input:focus{{border-color:var(--gold);}}
.fbar .lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-right:4px;}}
.fbar .count{{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--teal);margin-left:auto;}}

/* ── The log: compact boarding-pass rows grouped by year ── */
.log{{padding:18px 28px 80px;max-width:1300px;margin:0 auto;}}
.log-h{{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,3vw,2.3rem);font-weight:700;margin:26px 0 4px;}}
.log-h em{{font-style:normal;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
details.ly{{margin-bottom:12px;}}
details.ly>summary{{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:12px;padding:8px 0;}}
details.ly>summary::-webkit-details-marker{{display:none;}}
.ly-y{{font-family:'Playfair Display',serif;font-weight:900;font-size:2.2rem;line-height:1;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.ly-n{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);background:var(--card2);padding:3px 10px;border-radius:999px;}}
.ly-arrow{{color:var(--muted);transition:transform .2s;}}
details[open] .ly-arrow{{transform:rotate(90deg);}}
.fr{{display:grid;grid-template-columns:38px 70px minmax(0,1fr) 110px;gap:14px;align-items:center;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:10px 16px;margin-bottom:6px;position:relative;}}
.fr::before{{content:'';position:absolute;left:0;top:12px;bottom:12px;width:3px;border-radius:2px;background:linear-gradient(180deg,#f6d38b,#ff6fae);}}
.fc-logo{{width:38px;height:38px;background:rgba(255,255,255,.94);border-radius:9px;padding:3px;object-fit:contain;}}
.fc-logo-fb{{width:38px;height:38px;background:rgba(232,184,109,.15);border-radius:9px;display:flex;align-items:center;justify-content:center;color:var(--gold);}}
.fd b{{display:block;font-family:'DM Mono',monospace;font-size:.8rem;font-weight:500;}}
.fd small,.fm small{{display:block;font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);}}
.fx{{display:grid;grid-template-columns:minmax(0,1fr) 120px minmax(0,1fr);align-items:center;gap:10px;min-width:0;}}
.io b{{display:block;font-family:'Playfair Display',serif;font-weight:900;font-size:1.5rem;line-height:1;color:var(--gold);}}
.io small{{display:block;font-size:.66rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.io.r{{text-align:right;}}
.fl{{text-align:center;font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);}}
.fl i{{display:block;height:1px;background:linear-gradient(90deg,transparent,#ff7a6b,transparent);margin-bottom:5px;position:relative;}}
.fl i::after{{content:'✈';position:absolute;left:50%;top:-8px;transform:translateX(-50%);color:#ff7a6b;font-size:.8rem;font-style:normal;}}
.fm{{font-family:'DM Mono',monospace;font-size:.7rem;text-align:right;}}
.fnote{{grid-column:3/-1;font-style:italic;font-size:.72rem;color:var(--muted);}}
.empty{{text-align:center;padding:40px;color:var(--muted);}}
@media(max-width:600px){{.hero{{padding:48px 16px 6px;}}.map-section,.facts,.log,.records-section,.yearly-section,.tops-section,.dow-section{{padding-left:14px;padding-right:14px;}}#flMap{{height:340px;border-radius:18px;}}
.fr{{grid-template-columns:32px minmax(0,1fr);gap:10px;padding:10px 12px;}}.fc-logo,.fc-logo-fb{{width:32px;height:32px;}}.fd,.fm{{display:none;}}.fx{{grid-template-columns:minmax(0,1fr) 70px minmax(0,1fr);}}.io b{{font-size:1.2rem;}}.huge{{font-size:2rem;}}.fnote{{grid-column:2;}}}}

/* ── Side nav ── */
.side-nav{{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:4000;display:flex;flex-direction:column;background:rgba(18,21,31,0.92);border:1px solid var(--border);border-right:none;border-radius:10px 0 0 10px;backdrop-filter:blur(10px);overflow:hidden;}}
.side-nav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;width:52px;padding:6px 4px;color:var(--muted);text-decoration:none;}}
.side-nav a:hover{{color:var(--text);background:rgba(255,255,255,.06);}}
.side-nav a.active{{color:var(--gold);}}
.side-nav .sn-icon{{font-size:.82rem;}}
.side-nav .sn-lbl{{font-family:'DM Mono',monospace;font-size:.42rem;text-transform:uppercase;letter-spacing:.05em;}}
@media(max-width:900px){{.side-nav{{display:none;}}}}
</style>
</head>
<body>

<a class="crumb" href="index.html">← Dashboard</a>

<div class="hero">
  <div class="eyebrow">Flights · ground truth from FlightRadar24</div>
  <h1>{total_flights} flights, <em>{total_km} km</em></h1>
  <div class="sub">Every leg on record — real great-circle arcs, brighter for routes flown more often; dots are airports, bigger for more visits.</div>
</div>

<div class="map-section"><div id="flMap"></div></div>

<div class="facts">
  <div class="box s2"><h3>Distance</h3><div class="huge" id="factEarth"></div><div class="fact-sub">around the Earth · {total_km} km</div></div>
  <div class="box s2"><h3>In the air</h3><div class="huge sea" id="factDays"></div><div class="fact-sub" id="factDaysSub"></div></div>
  <div class="box s2"><h3>Reach</h3><div class="huge mint">{n_airports}</div><div class="fact-sub">airports · {n_airlines} airlines · {n_routes} routes · {n_regs} airframes</div></div>
  <div class="box s4 grow"><h3>Flights per year</h3><div class="fy" id="factYears"></div></div>
  <div class="box s2"><h3>Top routes</h3><div id="factRoutes"></div></div>
  <div class="box s6"><h3>Airlines</h3><div class="al-grid" id="factAirlines"></div></div>
</div>

<div class="records-section">
  <div class="records-h">Records</div>
  <div class="records-title">The <em>edges</em> of the logbook</div>
  <div class="records-grid">{records_html}</div>
</div>

<div class="yearly-section">
  <div class="records-h">Yearly breakdown</div>
  <div class="records-title">Flights, distance &amp; hours by <em>year</em></div>
  <div class="yearly-wrap">
    <div class="yearly-head"><div>Year</div><div></div><div>Flights</div><div>Distance</div><div>Hours</div></div>
    {yearly_html}
  </div>
</div>

<div class="tops-section">{top_block_html}{tail_block_html}{busy_days_html}</div>

<div class="dow-section">
  <div class="records-h">Cadence</div>
  <div class="records-title">By <em>day of week</em></div>
  <div class="dow-chart">{dow_html}</div>
</div>

<div class="log" style="padding-bottom:0"><div class="log-h">The <em>log</em></div></div>
<div class="fbar">
  <span class="lbl">Year</span>
  <select id="fYear"><option value="">All</option></select>
  <span class="lbl">Airline</span>
  <select id="fAirline"><option value="">All</option></select>
  <span class="lbl">Aircraft</span>
  <select id="fAircraft"><option value="">All</option></select>
  <input id="fQ" type="text" placeholder="Search route / airport / note…" style="width:240px;">
  <span class="count" id="fCount"></span>
</div>

<div class="log"><div id="grid"></div></div>

<nav class="side-nav" aria-label="Page shortcuts">
  <a href="index.html"><span class="sn-icon">🏠</span><span class="sn-lbl">HOME</span></a>
  <a href="feed.html"><span class="sn-icon">📰</span><span class="sn-lbl">FEED</span></a>
  <a href="tips.html"><span class="sn-icon">💬</span><span class="sn-lbl">TIPS</span></a>
  <a href="ratings.html"><span class="sn-icon">⭐</span><span class="sn-lbl">RATED</span></a>
  <a href="shouts.html"><span class="sn-icon">💭</span><span class="sn-lbl">SHOUTS</span></a>
  <a href="flights.html" class="active"><span class="sn-icon">✈</span><span class="sn-lbl">FLIGHTS</span></a>
  <a href="trips.html"><span class="sn-icon">🧳</span><span class="sn-lbl">TRIPS</span></a>
  <a href="photos.html"><span class="sn-icon">📷</span><span class="sn-lbl">PHOTOS</span></a>
  <a href="stats.html"><span class="sn-icon">📊</span><span class="sn-lbl">STATS</span></a>
  <a href="compare.html"><span class="sn-icon">⚖️</span><span class="sn-lbl">COMPARE</span></a>
  <a href="life.html"><span class="sn-icon">🧬</span><span class="sn-lbl">LIFE</span></a>
  <a href="culture.html"><span class="sn-icon">🍷</span><span class="sn-lbl">CULTURE</span></a>
</nav>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
"use strict";
const FLIGHTS = {flights_json};
const COORDS  = {coords_json};
const HISTORY = {history_json};
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
// Multi-CDN airline-logo fallback chain.  Kiwi has best coverage; Google
// Flights covers majors; Airhex covers everything but sometimes blocks
// hotlinking.  Set src to first; onerror walks the list.
function setLogo(img, iata) {{
  if (!iata) {{ img.style.display='none'; const fb=img.nextElementSibling; if(fb)fb.style.display='flex'; return; }}
  const cdns = [
    `https://images.kiwi.com/airlines/64/${{iata}}.png`,
    `https://www.gstatic.com/flights/airline_logos/70px/${{iata}}.png`,
    `https://content.airhex.com/content/logos/airlines_${{iata}}_70_70_s.png`,
  ];
  let i = 0;
  img.onerror = () => {{ i++; if (i < cdns.length) img.src = cdns[i]; else {{ img.style.display='none'; const fb=img.nextElementSibling; if(fb)fb.style.display='flex'; }} }};
  img.src = cdns[0];
}}
const fmtDur = m => m ? `${{Math.floor(m/60)}}h ${{String(m%60).padStart(2,'0')}}` : '';
const haversine = (la1, lo1, la2, lo2) => {{
  const R = 6371, r = Math.PI/180;
  const dLa = (la2-la1)*r, dLo = (lo2-lo1)*r;
  const a = Math.sin(dLa/2)**2 + Math.cos(la1*r)*Math.cos(la2*r)*Math.sin(dLo/2)**2;
  return Math.round(R * 2 * Math.asin(Math.sqrt(a)));
}};

// Build great-circle polyline as a series of interpolated points
function greatCircle(la1, lo1, la2, lo2, steps) {{
  steps = steps || 64;
  const r = Math.PI/180;
  const φ1=la1*r, λ1=lo1*r, φ2=la2*r, λ2=lo2*r;
  const d = 2 * Math.asin(Math.sqrt(Math.sin((φ2-φ1)/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin((λ2-λ1)/2)**2));
  if (d === 0) return [[la1, lo1]];
  const pts = [];
  for (let i = 0; i <= steps; i++) {{
    const f = i/steps;
    const A = Math.sin((1-f)*d)/Math.sin(d);
    const B = Math.sin(f*d)/Math.sin(d);
    const x = A*Math.cos(φ1)*Math.cos(λ1) + B*Math.cos(φ2)*Math.cos(λ2);
    const y = A*Math.cos(φ1)*Math.sin(λ1) + B*Math.cos(φ2)*Math.sin(λ2);
    const z = A*Math.sin(φ1) + B*Math.sin(φ2);
    const φ = Math.atan2(z, Math.sqrt(x*x + y*y));
    const λ = Math.atan2(y, x);
    pts.push([φ/r, λ/r]);
  }}
  return pts;
}}

// ── Map ──
const map = L.map('flMap', {{ zoomControl: true, worldCopyJump: true }}).setView([35, 20], 2);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={{{{CARTO_KEY}}}}', {{ attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 18 }}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const airportLayer = L.layerGroup().addTo(map);

// Routes collapsed to undirected pairs: colour and weight grow with how often a route was flown
const pairCount = new Map(), apCount = new Map();
FLIGHTS.forEach(f => {{
  if (!f.from_iata || !f.to_iata) return;
  const key = [f.from_iata, f.to_iata].sort().join('|');
  pairCount.set(key, (pairCount.get(key) || 0) + 1);
  [f.from_iata, f.to_iata].forEach(a => apCount.set(a, (apCount.get(a) || 0) + 1));
}});
const maxPair = Math.max(1, ...pairCount.values()), maxAp = Math.max(1, ...apCount.values());
const pairColor = n => n > 4 ? '#ff9a6b' : n > 1 ? '#ff6fae' : '#9b7bff';
pairCount.forEach((n, key) => {{
  const [a, b] = key.split('|');
  const ca = COORDS[a], cb = COORDS[b];
  if (!ca || !cb) return;
  const pts = greatCircle(ca[0], ca[1], cb[0], cb[1]);
  L.polyline(pts, {{ color: pairColor(n), weight: 6 + 6 * n / maxPair, opacity: 0.08 }}).addTo(routeLayer);   // glow
  L.polyline(pts, {{ color: pairColor(n), weight: 1 + 2.4 * n / maxPair, opacity: 0.55 + 0.4 * n / maxPair }})
    .addTo(routeLayer).bindPopup(`<strong>${{a}} ⇄ ${{b}}</strong><br>${{n}} flight${{n === 1 ? '' : 's'}}`);
}});
const fitPts = [];
Object.entries(COORDS).forEach(([iata, [la, lo, name]]) => {{
  if (!la || !lo || !apCount.get(iata)) return;
  const n = apCount.get(iata); fitPts.push([la, lo]);
  const m = L.circleMarker([la, lo], {{ radius: 2.5 + 6 * Math.sqrt(n / maxAp), fillColor: '#f6d38b', color: '#ffb36b', weight: 1, fillOpacity: 0.95 }})
    .addTo(airportLayer).bindPopup(`<strong>${{iata}}</strong><br>${{esc(name)}}<br>${{n}} departures + arrivals`);
  if (n >= 8) m.bindTooltip(iata, {{ permanent: true, direction: 'right', className: 'ap-lbl', offset: [6, 0] }});
}});
if (fitPts.length) map.fitBounds(fitPts, {{ padding: [24, 24] }});

// ── Facts ──
const EARTH_KM = 40075;
document.getElementById('factEarth').textContent = (HISTORY.total_km / EARTH_KM).toFixed(1) + '×';
document.getElementById('factDays').textContent = (HISTORY.total_hours / 24).toFixed(1) + ' days';
document.getElementById('factDaysSub').textContent = Math.round(HISTORY.total_hours).toLocaleString() + ' hours · average ' + Math.round(HISTORY.total_hours * 60 / Math.max(1, FLIGHTS.length)) + ' min a leg';
const byYear = HISTORY.by_year || [], maxY = Math.max(1, ...byYear.map(r => r[1]));
document.getElementById('factYears').innerHTML = byYear.map(r => `<div class="fy-col" title="${{r[0]}}: ${{r[1]}} flights · ${{r[2].toLocaleString()}} km · ${{r[3]}} h"><span class="v">${{r[1]}}</span><span class="t"><i style="height:${{Math.max(3, Math.round(100 * r[1] / maxY))}}%"></i></span><span class="l">${{r[0].slice(2)}}</span></div>`).join('');
document.getElementById('factRoutes').innerHTML = [...pairCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  .map(([k, n]) => {{ const [a, b] = k.split('|'); return `<div class="rt">${{a}}<span>⇄</span>${{b}}<em>${{n}}×</em></div>`; }}).join('');
const alCount = new Map(), alIata = {{}};
FLIGHTS.forEach(f => {{ if (!f.airline) return; alCount.set(f.airline, (alCount.get(f.airline) || 0) + 1); if (f.airline_iata) alIata[f.airline] = f.airline_iata; }});
const alTop = [...alCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, 9), alMax = alTop.length ? alTop[0][1] : 1;
document.getElementById('factAirlines').innerHTML = alTop.map(([a, n], i) => `<div class="al"><img class="lg" id="al_logo_${{i}}" alt=""><span class="lgfb">✈</span><span class="nm">${{esc(a)}}</span><span class="bar"><i style="width:${{Math.round(100 * n / alMax)}}%"></i></span><b>${{n}}</b></div>`).join('');
alTop.forEach(([a], i) => setLogo(document.getElementById('al_logo_' + i), alIata[a]));

// ── Filters ──
const years = [...new Set(FLIGHTS.map(f => f.date.slice(0,4)))].sort();
const airlines = [...new Set(FLIGHTS.map(f => f.airline).filter(Boolean))].sort();
const aircraft = [...new Set(FLIGHTS.map(f => f.aircraft_code || f.aircraft).filter(Boolean))].sort();
const yrSel = document.getElementById('fYear'); years.reverse().forEach(y => yrSel.appendChild(new Option(y, y)));
const alSel = document.getElementById('fAirline'); airlines.forEach(a => alSel.appendChild(new Option(a, a)));
const acSel = document.getElementById('fAircraft'); aircraft.forEach(a => acSel.appendChild(new Option(a, a)));

function getFiltered() {{
  const yr = yrSel.value, al = alSel.value, ac = acSel.value;
  const q = document.getElementById('fQ').value.toLowerCase().trim();
  return FLIGHTS.filter(f => {{
    if (yr && f.date.slice(0,4) !== yr) return false;
    if (al && f.airline !== al) return false;
    if (ac && (f.aircraft_code || f.aircraft) !== ac) return false;
    if (q) {{
      const blob = `${{f.flight}} ${{f.from_iata}} ${{f.to_iata}} ${{f.from_name}} ${{f.to_name}} ${{f.airline}} ${{f.aircraft}} ${{f.reg}} ${{f.note}}`.toLowerCase();
      if (!blob.includes(q)) return false;
    }}
    return true;
  }});
}}

function flightRow(f, idx) {{
  const ca = COORDS[f.from_iata], cb = COORDS[f.to_iata];
  const km = (ca && cb) ? haversine(ca[0], ca[1], cb[0], cb[1]) : 0;
  return `<div class="fr">
    ${{f.airline_iata ? `<img class="fc-logo" id="fc_logo_${{idx}}" alt="" title="${{esc(f.airline)}}"/><div class="fc-logo-fb" style="display:none;">✈</div>` : `<div class="fc-logo-fb" title="${{esc(f.airline || '')}}">✈</div>`}}
    <div class="fd"><b>${{f.date.slice(8, 10)}}.${{f.date.slice(5, 7)}}</b><small>${{esc(f.flight || '')}}</small></div>
    <div class="fx">
      <div class="io"><b>${{esc(f.from_iata || '—')}}</b><small>${{esc(f.from_name || '')}}</small></div>
      <div class="fl"><i></i>${{fmtDur(f.dur_min)}}${{km ? ' · ' + km.toLocaleString() + ' km' : ''}}</div>
      <div class="io r"><b>${{esc(f.to_iata || '—')}}</b><small>${{esc(f.to_name || '')}}</small></div>
    </div>
    <div class="fm">${{esc(f.aircraft_code || f.aircraft || '')}}<small>${{esc(f.reg || '')}}${{f.seat ? ' · ' + esc(f.seat) : ''}}</small></div>
    ${{f.note ? `<div class="fnote">“${{esc(f.note)}}”</div>` : ''}}
  </div>`;
}}
function render() {{
  const filtered = getFiltered();
  const filtering = yrSel.value || alSel.value || acSel.value || document.getElementById('fQ').value.trim();
  document.getElementById('fCount').textContent = `${{filtered.length}} / ${{FLIGHTS.length}}`;
  const grid = document.getElementById('grid');
  if (!filtered.length) {{ grid.innerHTML = '<div class="empty">No flights match</div>'; return; }}
  const byY = {{}};
  filtered.forEach((f, idx) => (byY[f.date.slice(0, 4)] = byY[f.date.slice(0, 4)] || []).push([f, idx]));
  grid.innerHTML = Object.keys(byY).sort((a, b) => b - a).map((y, yi) =>
    `<details class="ly"${{filtering || yi < 2 ? ' open' : ''}}><summary><span class="ly-y">${{y}}</span><span class="ly-n">${{byY[y].length}} flight${{byY[y].length === 1 ? '' : 's'}}</span><span class="ly-arrow">›</span></summary>
      ${{byY[y].map(([f, idx]) => flightRow(f, idx)).join('')}}</details>`).join('');
  // Boot the logo loaders after innerHTML is set
  filtered.forEach((f, idx) => {{
    if (!f.airline_iata) return;
    const el = document.getElementById(`fc_logo_${{idx}}`);
    if (el) setLogo(el, f.airline_iata);
  }});
}}

['fYear','fAirline','fAircraft'].forEach(id => document.getElementById(id).addEventListener('change', render));
document.getElementById('fQ').addEventListener('input', render);
render();

(function(){{document.body.classList.toggle('light', localStorage.getItem('fsq-theme') === 'light');}})();
</script>
</body>
</html>
"""
