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
from pathlib import Path


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

    html = HTML.format(
        flights_json=flights_json,
        coords_json=coords_json,
        history_json=history_json,
        total_flights=flight_history.get("total_flights", len(slim)),
        total_hours=flight_history.get("total_hours", 0),
        total_km=f"{flight_history.get('total_km', 0):,}",
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
.kpi-strip{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-top:28px;max-width:1200px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:1.8rem;font-weight:700;color:var(--gold);line-height:1;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.55rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-top:5px;}}

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
    <div class="kpi"><div class="kpi-num" id="kpiAirports">—</div><div class="kpi-lbl">Unique airports</div></div>
  </div>
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
document.getElementById('kpiAirports').textContent = seenAirports.size;

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
