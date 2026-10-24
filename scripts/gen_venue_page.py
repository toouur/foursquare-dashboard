# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_venue_page.py — one page per venue, served as ``venue.html#<venue_id>``.

The page is a single static shell; the dossier comes from the venue's shard
``venue_data_<xx>.json`` (written here via `metrics.venues`), fetched on load and on
every hash change, cache-busted with a content hash. Without a hash it lists the
most visited venues. Every venue gets a dossier, `Home (private)` included — like the
rest of the site. Self-contained like the country pages (fonts + Leaflet from CDNs, no {{SITE_CSS_LINK}}); the only post-processed placeholder is {{CARTO_KEY}}.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from metrics.venues import compute_venues, write_venue_shards

log = logging.getLogger(__name__)

SITE_URL = "https://4sq.pages.dev"
TOP_N = 40

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Venue · Foursquare history</title>
<meta name="description" content="Every visit to one place: when, how often, with whom.">
<link rel="canonical" href="{site}/venue.html">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;700;900&family=DM+Sans:wght@400;500;600;700&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);}}
.crumb{{position:fixed;top:14px;left:18px;z-index:1200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}
.wrap{{max-width:960px;margin:0 auto;padding:88px 22px 120px;}}
.hero-cat{{font-family:'DM Mono',monospace;font-size:.62rem;text-transform:uppercase;letter-spacing:.18em;color:var(--teal);margin-bottom:10px;}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.1rem,5.5vw,3.8rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;overflow-wrap:anywhere;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:34px 0 12px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 14px;text-align:center;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:1.7rem;font-weight:700;color:var(--gold);line-height:1.1;}}
.kpi-num.sm{{font-size:1.15rem;padding:.3rem 0;white-space:nowrap;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.54rem;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin-top:7px;}}
section{{margin-top:48px;}}
.sec-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.2em;color:var(--gold);margin-bottom:16px;}}
.bars{{display:flex;align-items:flex-end;gap:5px;height:140px;padding:8px 0;overflow-x:auto;overflow-y:hidden;}}
.bar-col{{display:flex;flex-direction:column;align-items:center;gap:5px;flex:0 0 auto;min-width:30px;}}
.bar-col .bar{{width:20px;background:linear-gradient(180deg,#f5d48a,#b97c30);border-radius:4px 4px 0 0;min-height:2px;}}
.bar-col.h .bar{{background:linear-gradient(180deg,#7fe0cc,#2f8f7d);width:14px;}}
.bar-col.h{{min-width:22px;}}
.bar-lbl{{font-family:'DM Mono',monospace;font-size:.5rem;color:var(--muted);}}
.bar-n{{font-family:'DM Mono',monospace;font-size:.48rem;color:var(--teal);}}
#map{{height:260px;border-radius:14px;border:1px solid var(--border);background:var(--card2);}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:26px;}}
.rank-list{{display:flex;flex-direction:column;gap:4px;}}
.rank-row{{display:grid;grid-template-columns:22px 1fr auto;gap:8px;align-items:center;padding:5px 6px;border-radius:6px;color:var(--text);text-decoration:none;}}
.rank-row:hover{{background:var(--card2);}}
.rank-i{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);}}
.rank-name{{font-size:.86rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.rank-sub{{color:var(--muted);font-size:.72rem;}}
.rank-n{{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--teal);}}
.visit{{display:grid;grid-template-columns:1fr auto;gap:14px;padding:12px 0;border-bottom:1px solid var(--border);}}
.visit-when{{font-family:'DM Mono',monospace;font-size:.66rem;color:var(--gold);letter-spacing:.04em;}}
.visit-shout{{font-family:'Playfair Display',serif;font-style:italic;font-size:.98rem;color:#e8e9ee;margin-top:4px;overflow-wrap:anywhere;}}
.visit-with{{font-size:.74rem;color:var(--muted);margin-top:3px;}}
.visit img{{width:96px;height:72px;object-fit:cover;border-radius:8px;background:var(--card2);}}
.rf{{font-family:'DM Mono',monospace;font-size:.5rem;text-transform:uppercase;letter-spacing:.1em;color:var(--rose);border:1px solid var(--rose);border-radius:4px;padding:1px 5px;margin-left:8px;}}
.ext{{color:var(--teal);text-decoration:none;text-transform:none;letter-spacing:.04em;}}
.ext:hover{{color:var(--gold);}}
.note{{color:var(--muted);font-size:.85rem;margin-top:24px;}}
@media(max-width:600px){{.wrap{{padding:78px 16px 90px;}}.visit img{{width:72px;height:54px;}}}}
</style>
</head>
<body>
<a class="crumb" href="/index.html">← dashboard</a>
<div class="wrap" id="app"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const VER = {ver_json};
const PIX = {pix_json};
const TOP = {top_json};
const app = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const fmt = n => Number(n).toLocaleString('en-US');
// Mirrors metrics.venues.venue_shard (32-bit FNV-1a mod 256).
function venueShard(id) {{
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {{ h ^= id.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }}
  return (h & 0xff).toString(16).padStart(2, '0');
}}
const shardCache = {{}};
function loadShard(k) {{
  if (!shardCache[k]) shardCache[k] = fetch('venue_data_' + k + '.json?v=' + VER).then(r => {{
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }});
  return shardCache[k];
}}
function bars(pairs, cls) {{
  const mx = Math.max(1, ...pairs.map(p => p[1]));
  return '<div class="bars">' + pairs.map(([l, n]) =>
    '<div class="bar-col ' + cls + '" title="' + esc(l) + ': ' + n + '"><span class="bar-n">' + (n || '') + '</span>' +
    '<div class="bar" style="height:' + Math.round(100 * n / mx) + 'px"></div><span class="bar-lbl">' + esc(l) + '</span></div>').join('') + '</div>';
}}
let map = null;
function renderTop() {{
  document.title = 'Venues · Foursquare history';
  app.innerHTML = '<div class="hero-cat">Places</div><h1 class="hero-name">Most visited venues</h1>' +
    '<div class="hero-sub">open any venue as venue.html#&lt;venue id&gt;</div><section><div class="rank-list">' +
    TOP.map((t, i) => '<a class="rank-row" href="#' + esc(t.id) + '"><span class="rank-i">' + (i + 1) + '</span>' +
      '<span class="rank-name">' + esc(t.name) + ' <span class="rank-sub">· ' + esc(t.city) + '</span></span>' +
      '<span class="rank-n">' + fmt(t.n) + '</span></a>').join('') + '</div></section>';
}}
function render(id, rec, f) {{
  const ix = Object.fromEntries(f.map((k, i) => [k, i]));
  const place = [rec.city, rec.country].filter(Boolean).join(', ');
  document.title = rec.name + ' · Foursquare history';
  const yrs = Object.entries(rec.years);
  const span = yrs.length ? (+yrs[yrs.length - 1][0] - +yrs[0][0] + 1) : 0;
  let h = '<div class="hero-cat">' + esc(rec.cat || 'Venue') + '</div><h1 class="hero-name">' + esc(rec.name) + '</h1>' +
    '<div class="hero-sub">' + esc(place) + ' · <a class="ext" href="https://foursquare.com/v/' + encodeURIComponent(id) + '" target="_blank" rel="noopener">Open on Foursquare ↗</a></div><div class="kpis">' +
    [[fmt(rec.n), rec.n === 1 ? 'visit' : 'visits'], [rec.first, 'first visit'], [rec.last, 'last visit'],
     [span, span === 1 ? 'year' : 'years span'], [fmt(rec.companions.length), 'companions'], [fmt(rec.photos), 'photos']]
      .map(([a, b]) => '<div class="kpi"><div class="kpi-num' + (/^\d{{4}}-/.test(a) ? ' sm' : '') + '">' + esc(a) + '</div><div class="kpi-lbl">' + esc(b) + '</div></div>').join('') + '</div>';
  if (rec.lat != null && rec.lng != null) h += '<section><div class="sec-h">Where</div><div id="map"></div></section>';
  if (yrs.length > 1) h += '<section><div class="sec-h">Visits per year</div>' + bars(yrs, '') + '</section>';
  if (rec.n > 2) h += '<section><div class="sec-h">Local hour of day</div>' + bars(rec.hours.map((n, i) => [String(i).padStart(2, '0'), n]), 'h') + '</section>';
  if (rec.companions.length) h += '<section><div class="sec-h">Here with</div><div class="rank-list">' +
    rec.companions.map(([nm, n], i) => '<div class="rank-row"><span class="rank-i">' + (i + 1) + '</span><span class="rank-name">' +
      esc(nm) + '</span><span class="rank-n">' + fmt(n) + '</span></div>').join('') + '</div></section>';
  h += '<section><div class="sec-h">' + (rec.v.length < rec.n ? 'Latest ' + rec.v.length + ' of ' + fmt(rec.n) + ' visits' : 'Visits') + '</div>' +
    rec.v.map(v => '<div class="visit"><div><div class="visit-when">' + esc(v[ix.local]) + (v[ix.rf] ? '<span class="rf">↺ reconstructed</span>' : '') + '</div>' +
      (v[ix.shout] ? '<div class="visit-shout">' + esc(v[ix.shout]) + '</div>' : '') +
      (v[ix.with].length ? '<div class="visit-with">with ' + esc(v[ix.with].join(', ')) + '</div>' : '') + '</div>' +
      (v[ix.photo] && PIX ? '<img loading="lazy" src="' + esc(PIX + '/' + v[ix.photo]) + '" alt="">' : '<span></span>') + '</div>').join('') + '</section>';
  app.innerHTML = h;
  if (map) {{ map.remove(); map = null; }}
  if (document.getElementById('map') && window.L) {{
    map = L.map('map', {{ scrollWheelZoom: false }}).setView([rec.lat, rec.lng], 15);
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={{{{CARTO_KEY}}}}', {{ attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 19 }}).addTo(map);
    L.circleMarker([rec.lat, rec.lng], {{ radius: 8, color: '#e8b86d', fillColor: '#e8b86d', fillOpacity: .8 }}).addTo(map);
  }}
}}
async function route() {{
  const id = decodeURIComponent(location.hash.slice(1)).trim();
  if (!id) {{ renderTop(); return; }}
  app.innerHTML = '<p class="note">Loading…</p>';
  try {{
    const j = await loadShard(venueShard(id));
    const rec = j.v[id];
    if (!rec) {{
      document.title = 'Venue · Foursquare history';
      app.innerHTML = '<div class="hero-cat">Venue</div><h1 class="hero-name">Not checked in here yet</h1>' +
        '<p class="note">There are no check-ins at this venue in the history (a tip or a list can point at a place never visited). ' +
        '<a href="https://foursquare.com/v/' + encodeURIComponent(id) + '" target="_blank" rel="noopener">Open it on Foursquare ↗</a> · <a href="#">Most visited venues</a></p>';
      return;
    }}
    render(id, rec, j.f);
    window.scrollTo(0, 0);
  }} catch (e) {{
    app.innerHTML = '<p class="note">Could not load this venue (' + esc(e.message) + ').</p>';
  }}
}}
window.addEventListener('hashchange', route);
route();
</script>
</body>
</html>"""


def build_page(
    out_path: str = "",
    rows: list | None = None,
    photos_by_checkin: dict | None = None,
    pix_url: str = "",
    **_extra,
) -> int:
    """Write venue.html + venue_data_<xx>.json shards. Returns the venue count."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    venues = compute_venues(rows or [], photos_by_checkin)
    ver = write_venue_shards(venues, str(out_dir))
    top = sorted(venues.items(), key=lambda kv: (-kv[1]["n"], kv[1]["name"]))[:TOP_N]
    top_json = json.dumps([{"id": vid, "name": r["name"], "city": r["city"], "n": r["n"]}
                           for vid, r in top], ensure_ascii=False).replace("</", "<\\/")
    page = PAGE_TEMPLATE.format(
        site=SITE_URL,
        ver_json=json.dumps(ver),
        pix_json=json.dumps((pix_url or "").rstrip("/")),
        top_json=top_json,
    )
    (out_dir / "venue.html").write_text(page, encoding="utf-8")
    log.info("venue.html: %d venues sharded, version %s", len(venues), ver)
    return len(venues)
