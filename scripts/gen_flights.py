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
    drives the expanded KPI strip and the records section on flights.html.

    Records are tuple-of-records so ties don't drop entries: we surface the
    single longest leg, the shortest, the slowest cruise, the busiest year,
    etc."""
    n_total = len(flights)
    airlines: Counter = Counter()
    aircraft_types: Counter = Counter()
    airports: Counter = Counter()
    routes_dir: Counter = Counter()      # directed (A→B ≠ B→A)
    routes_pair: Counter = Counter()     # undirected
    countries: set = set()
    years: Counter = Counter()
    dow: Counter = Counter()
    durs: list[int] = []
    legs: list[tuple[int, dict]] = []    # (km, flight)

    for f in flights:
        if f.get("airline"):
            airlines[f["airline"]] += 1
        ac = f.get("aircraft_code") or f.get("aircraft") or ""
        if ac:
            aircraft_types[ac] += 1
        fa, ta = f.get("from_iata", ""), f.get("to_iata", "")
        if fa: airports[fa] += 1
        if ta: airports[ta] += 1
        if fa and ta:
            routes_dir[(fa, ta)] += 1
            routes_pair[tuple(sorted([fa, ta]))] += 1
            ca, cb = iata_coords.get(fa), iata_coords.get(ta)
            if ca and cb and ca[0] and cb[0]:
                km = _hav_km(ca[0], ca[1], cb[0], cb[1])
                legs.append((km, f))
        dur = f.get("dur_min") or 0
        if dur > 0:
            durs.append(dur)
        try:
            d = datetime.strptime(f.get("date", "")[:10], "%Y-%m-%d")
            years[d.year] += 1
            dow[d.weekday()] += 1   # Monday = 0
        except (ValueError, TypeError):
            pass

    legs.sort(key=lambda x: -x[0])
    longest = legs[0] if legs else None
    shortest = legs[-1] if legs else None
    busiest_yr = years.most_common(1)[0] if years else (None, 0)
    avg_dur = round(sum(durs) / len(durs)) if durs else 0
    longest_dur = max(durs) if durs else 0

    return {
        "n_total": n_total,
        "n_airlines": len(airlines),
        "n_aircraft_types": len(aircraft_types),
        "n_airports": len(airports),
        "n_routes_dir": len(routes_dir),
        "n_routes_pair": len(routes_pair),
        "avg_dur_min": avg_dur,
        "longest_dur_min": longest_dur,
        "busiest_year": busiest_yr,
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
        "top_airlines": airlines.most_common(5),
        "top_aircraft": aircraft_types.most_common(5),
        "top_routes": routes_pair.most_common(5),
        "top_airports": airports.most_common(5),
        "dow": [dow.get(i, 0) for i in range(7)],
        "years": [(y, n) for y, n in sorted(years.items())],
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

    # Top lists rendered server-side
    def _list_block(label, items, fmt=None):
        if not items:
            return ""
        lines = []
        for i, (name, n) in enumerate(items, 1):
            if fmt == "route":
                name = " ↔ ".join(name)
            lines.append(
                f'<div class="ti-row"><span class="ti-rank">#{i}</span>'
                f'<span class="ti-name">{_esc(name)}</span>'
                f'<span class="ti-n">{n}×</span></div>'
            )
        return (
            f'<div class="ti-block"><div class="ti-lbl">{label}</div>'
            f'<div class="ti-list">{"".join(lines)}</div></div>'
        )

    top_block_html = (
        _list_block("Top airlines", stats["top_airlines"])
        + _list_block("Top routes", stats["top_routes"], fmt="route")
        + _list_block("Top airports", stats["top_airports"])
        + _list_block("Top aircraft", stats["top_aircraft"])
    )

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
        records_html=records_html,
        top_block_html=top_block_html,
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

/* ── Hero ── */
.hero{{padding:60px 28px 42px;background:linear-gradient(160deg,#10121c 0%,#0a0c12 75%);border-bottom:1px solid var(--border);position:relative;overflow:hidden;}}
.hero::before{{content:'';position:absolute;top:-50%;right:-20%;width:80%;height:160%;background:radial-gradient(closest-side,rgba(232,184,109,.10),transparent);pointer-events:none;}}
.hero h1{{font-family:'Playfair Display',serif;font-size:clamp(2rem,5vw,3.6rem);font-weight:900;background:linear-gradient(140deg,#f5d48a,#e8b86d 60%,#b97c30);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:-0.02em;}}
.hero .sub{{font-family:'DM Mono',monospace;font-size:.66rem;color:var(--muted);letter-spacing:.14em;text-transform:uppercase;margin-top:6px;}}
.kpi-strip{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:28px;max-width:1300px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;position:relative;overflow:hidden;}}
.kpi::after{{content:'';position:absolute;top:0;left:18px;right:18px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.35;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:1.75rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:5px;}}
.kpi-sub{{font-size:.62rem;color:var(--muted);margin-top:3px;opacity:.85;}}

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
.tops-section{{padding:30px 28px 0;max-width:1300px;margin:0 auto;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;}}
.ti-block{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;}}
.ti-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:12px;}}
.ti-list{{display:flex;flex-direction:column;gap:5px;}}
.ti-row{{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);}}
.ti-row:last-child{{border-bottom:none;}}
.ti-rank{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);width:22px;text-align:right;flex-shrink:0;}}
.ti-name{{flex:1;font-size:.76rem;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.ti-n{{font-family:'DM Mono',monospace;font-size:.68rem;color:var(--teal);flex-shrink:0;}}

/* ── Day-of-week chart ── */
.dow-section{{padding:30px 28px 16px;max-width:1300px;margin:0 auto;}}
.dow-chart{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 18px 14px;display:grid;grid-template-columns:repeat(7,1fr);gap:10px;height:200px;align-items:end;}}
.dow-col{{display:flex;flex-direction:column;align-items:center;gap:6px;height:100%;justify-content:flex-end;cursor:default;}}
.dow-bar{{width:100%;max-width:36px;background:linear-gradient(180deg,#f5d48a 0%,#e8b86d 60%,#b97c30);border-radius:6px 6px 2px 2px;min-height:4px;transition:filter .15s;box-shadow:0 0 10px rgba(232,184,109,.18);}}
.dow-col:hover .dow-bar{{filter:brightness(1.2);}}
.dow-num{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--text);font-weight:600;}}
.dow-lbl{{font-family:'DM Mono',monospace;font-size:.5rem;color:var(--muted);letter-spacing:.1em;}}

/* ── Map ── */
.map-section{{padding:32px 28px 16px;max-width:1400px;margin:0 auto;}}
#flMap{{width:100%;height:480px;border-radius:14px;background:#0c0e14;border:1px solid var(--border);}}
.leaflet-container{{background:#0c0e14;}}
.leaflet-popup-content-wrapper{{background:var(--card2);border:1px solid var(--border);color:var(--text);font-family:'DM Sans',sans-serif;font-size:.78rem;}}
.leaflet-popup-tip{{background:var(--card2);}}

/* ── Filter bar ── */
.fbar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:18px 28px;background:var(--card);border-bottom:1px solid var(--border);max-width:1400px;margin:0 auto;border-radius:14px;margin-top:16px;}}
.fbar select,.fbar input{{background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:6px 12px;color:var(--text);font-size:.78rem;font-family:'DM Sans',sans-serif;outline:none;cursor:pointer;}}
.fbar select:focus,.fbar input:focus{{border-color:var(--gold);}}
.fbar .lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-right:4px;}}
.fbar .count{{font-family:'DM Mono',monospace;font-size:.65rem;color:var(--teal);margin-left:auto;}}

/* ── Card grid ── */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;padding:18px 28px 80px;max-width:1400px;margin:0 auto;}}
.fcard{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 20px 16px;display:flex;flex-direction:column;gap:12px;transition:transform .2s,border-color .2s;}}
.fcard:hover{{border-color:rgba(232,184,109,.4);transform:translateY(-2px);}}
.fc-head{{display:flex;align-items:center;gap:14px;}}
.fc-logo{{width:44px;height:44px;background:rgba(255,255,255,.94);border-radius:8px;padding:4px;object-fit:contain;flex-shrink:0;}}
.fc-logo-fb{{width:44px;height:44px;background:rgba(232,184,109,.15);border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--gold);font-size:1.3rem;flex-shrink:0;}}
.fc-airline-block{{flex:1;min-width:0;}}
.fc-airline{{font-size:.86rem;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.fc-fno{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--teal);margin-top:1px;letter-spacing:.05em;}}
.fc-route{{font-family:'Playfair Display',serif;font-size:1.5rem;font-weight:700;color:var(--gold);line-height:1.05;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;}}
.fc-route .iata{{color:var(--gold);}}
.fc-route .arrow{{font-family:'DM Sans',sans-serif;font-size:1.1rem;color:var(--muted);font-weight:400;}}
.fc-route-names{{font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:-4px;}}
.fc-meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,.06);}}
.fc-meta-item{{display:flex;flex-direction:column;gap:1px;}}
.fc-meta-lbl{{font-family:'DM Mono',monospace;font-size:.5rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);}}
.fc-meta-val{{font-family:'DM Mono',monospace;font-size:.7rem;color:var(--text);font-weight:500;}}
.fc-footer{{display:flex;justify-content:space-between;align-items:center;gap:8px;font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);}}
.fc-note{{font-style:italic;font-size:.7rem;color:var(--muted);margin-top:4px;padding-top:8px;border-top:1px dashed rgba(255,255,255,.06);}}

@media(max-width:600px){{.hero{{padding:42px 16px 30px;}}.kpi-strip{{grid-template-columns:repeat(2,1fr);}}.map-section,.grid{{padding-left:14px;padding-right:14px;}}.grid{{grid-template-columns:1fr;}}}}

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
  <h1>Flights</h1>
  <div class="sub">Every leg, ground-truth from FlightRadar24</div>
  <div class="kpi-strip">
    <div class="kpi"><div class="kpi-num">{total_flights}</div><div class="kpi-lbl">Flights</div></div>
    <div class="kpi"><div class="kpi-num">{total_hours} h</div><div class="kpi-lbl">In the air</div></div>
    <div class="kpi"><div class="kpi-num">{total_km} km</div><div class="kpi-lbl">Distance flown</div></div>
    <div class="kpi"><div class="kpi-num">{n_airports}</div><div class="kpi-lbl">Airports</div></div>
    <div class="kpi"><div class="kpi-num">{n_airlines}</div><div class="kpi-lbl">Airlines</div></div>
    <div class="kpi"><div class="kpi-num">{n_aircraft}</div><div class="kpi-lbl">Aircraft types</div></div>
    <div class="kpi"><div class="kpi-num">{n_routes}</div><div class="kpi-lbl">Unique routes</div><div class="kpi-sub">undirected pairs</div></div>
  </div>
</div>

<div class="records-section">
  <div class="records-h">Records</div>
  <div class="records-title">The <em>edges</em> of the logbook</div>
  <div class="records-grid">{records_html}</div>
</div>

<div class="tops-section">{top_block_html}</div>

<div class="dow-section">
  <div class="records-h">Cadence</div>
  <div class="records-title">By <em>day of week</em></div>
  <div class="dow-chart">{dow_html}</div>
</div>

<div class="map-section">
  <div id="flMap"></div>
</div>

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

<div class="grid" id="grid"></div>

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
const fmtDur = m => m ? `${{Math.floor(m/60)}}h ${{m%60}}m` : '';
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
const map = L.map('flMap', {{ zoomControl: true, attributionControl: false, worldCopyJump: true }}).setView([35, 20], 2);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{ subdomains: 'abcd', maxZoom: 18 }}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const airportLayer = L.layerGroup().addTo(map);

// Plot all airports as small dots
const seenAirports = new Set();
Object.entries(COORDS).forEach(([iata, [la, lo, name]]) => {{
  if (!la || !lo) return;
  seenAirports.add(iata);
  L.circleMarker([la, lo], {{ radius: 3, fillColor: '#e8b86d', color: '#fff', weight: 1, fillOpacity: 0.9 }})
    .addTo(airportLayer)
    .bindPopup(`<strong>${{iata}}</strong><br>${{esc(name)}}`);
}});

// Plot routes — collapsed to undirected pairs with thickness ∝ frequency
const pairCount = new Map();
FLIGHTS.forEach(f => {{
  if (!f.from_iata || !f.to_iata) return;
  const key = [f.from_iata, f.to_iata].sort().join('|');
  pairCount.set(key, (pairCount.get(key) || 0) + 1);
}});
pairCount.forEach((n, key) => {{
  const [a, b] = key.split('|');
  const ca = COORDS[a], cb = COORDS[b];
  if (!ca || !cb) return;
  const w = Math.min(3.5, 0.6 + n * 0.3);
  L.polyline(greatCircle(ca[0], ca[1], cb[0], cb[1]), {{ color: '#4ec9b0', weight: w, opacity: 0.55 }}).addTo(routeLayer);
}});

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

function render() {{
  const filtered = getFiltered();
  document.getElementById('fCount').textContent = `${{filtered.length}} / ${{FLIGHTS.length}}`;
  const grid = document.getElementById('grid');
  grid.innerHTML = filtered.map((f, idx) => {{
    const ca = COORDS[f.from_iata], cb = COORDS[f.to_iata];
    const km = (ca && cb) ? haversine(ca[0], ca[1], cb[0], cb[1]) : 0;
    return `<div class="fcard">
      <div class="fc-head">
        ${{f.airline_iata
            ? `<img class="fc-logo" id="fc_logo_${{idx}}" alt=""/><div class="fc-logo-fb" style="display:none;">✈</div>`
            : `<div class="fc-logo-fb">✈</div>`}}
        <div class="fc-airline-block">
          <div class="fc-airline">${{esc(f.airline || 'Unknown')}}</div>
          ${{f.flight ? `<div class="fc-fno">${{esc(f.flight)}}</div>` : ''}}
        </div>
      </div>
      <div>
        <div class="fc-route">
          <span class="iata">${{esc(f.from_iata || '—')}}</span>
          <span class="arrow">→</span>
          <span class="iata">${{esc(f.to_iata || '—')}}</span>
        </div>
        <div class="fc-route-names">${{esc(f.from_name || '')}} → ${{esc(f.to_name || '')}}</div>
      </div>
      <div class="fc-meta">
        <div class="fc-meta-item"><span class="fc-meta-lbl">Date</span><span class="fc-meta-val">${{esc(f.date)}}</span></div>
        ${{f.aircraft_code || f.aircraft ? `<div class="fc-meta-item"><span class="fc-meta-lbl">Plane</span><span class="fc-meta-val">${{esc(f.aircraft_code || f.aircraft)}}</span></div>` : ''}}
        ${{f.dur_min ? `<div class="fc-meta-item"><span class="fc-meta-lbl">Time</span><span class="fc-meta-val">${{fmtDur(f.dur_min)}}</span></div>` : ''}}
      </div>
      <div class="fc-footer">
        ${{f.reg ? `<span>Reg ${{esc(f.reg)}}</span>` : '<span>&nbsp;</span>'}}
        ${{km ? `<span>${{km.toLocaleString()}} km</span>` : ''}}
      </div>
      ${{f.note ? `<div class="fc-note">"${{esc(f.note)}}"</div>` : ''}}
    </div>`;
  }}).join('');
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
