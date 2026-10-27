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
.pgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;}}
.pgrid img{{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;background:var(--card2);cursor:pointer;transition:transform .15s,opacity .15s;}}
.pgrid img:hover{{transform:scale(1.03);opacity:.9;}}
details.yr{{background:var(--card);border:1px solid var(--border);border-radius:14px;margin-bottom:10px;overflow:hidden;}}
details.yr>summary{{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:14px;padding:14px 18px;}}
details.yr>summary::-webkit-details-marker{{display:none;}}
details.yr>summary:hover{{background:var(--card2);}}
.yr-y{{font-family:'Playfair Display',serif;font-size:1.5rem;font-weight:700;color:var(--gold);}}
.yr-meta{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.06em;flex:1;}}
.yr-strip{{display:flex;gap:2px;align-items:flex-end;height:18px;}}
.yr-strip i{{display:block;width:5px;background:var(--teal);opacity:.7;border-radius:1px;min-height:1px;}}
.yr-arrow{{color:var(--muted);transition:transform .2s;}}
details[open] .yr-arrow{{transform:rotate(90deg);}}
.hist{{max-width:860px;}}
.log{{padding:2px 18px 14px;}}
.mh{{display:flex;align-items:center;gap:10px;margin:14px 0 4px;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--gold);}}
.mh::after{{content:'';flex:1;height:1px;background:var(--border);order:1;}}
.mh span{{order:2;color:var(--muted);letter-spacing:.04em;}}
.vr{{display:grid;grid-template-columns:58px 1fr auto;gap:0 16px;align-items:center;min-height:44px;padding:6px 10px;margin:0 -10px;border-radius:8px;}}
.vr+.vr{{box-shadow:0 -1px 0 rgba(255,255,255,.035);}}
.vr:hover{{background:var(--card2);box-shadow:none;}}
.vr:hover+.vr{{box-shadow:none;}}
.vr-d{{font-family:'DM Mono',monospace;white-space:nowrap;line-height:1.15;}}
.vr-day{{font-size:.72rem;color:var(--muted);}}
.vr-day b{{font-weight:500;color:var(--text);display:inline-block;min-width:1.3em;text-align:right;margin-left:3px;}}
.vr-d.we .vr-day{{color:var(--gold);opacity:.85;}}
.vr.rfv .vr-day b{{color:var(--rose);}}
.vr-t{{font-size:.58rem;color:var(--muted);opacity:.75;margin-top:3px;letter-spacing:.04em;}}
.vr-x{{font-size:.88rem;color:var(--text);min-width:0;overflow-wrap:anywhere;line-height:1.4;}}
.vr-x .w{{display:block;color:var(--muted);font-size:.76rem;margin-top:1px;}}
.vr-x .w:first-child{{margin-top:0;font-size:.82rem;}}
.vr-x .w b{{font-weight:400;color:var(--teal);}}
.vr-x .lk{{display:block;font-family:'DM Mono',monospace;font-size:.6rem;line-height:1.5;color:#ff8fbf;margin-top:2px;}}
.vr-x .lk i{{font-style:normal;color:var(--muted);}}
.vr-x .rf{{margin-left:0;}}
.vr-p{{display:flex;gap:4px;}}
.vr-p span{{position:relative;display:block;width:52px;height:39px;border-radius:5px;overflow:hidden;cursor:pointer;border:1px solid var(--border);background:var(--card2);}}
.vr-p img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s;}}
.vr-p span:hover img{{transform:scale(1.08);}}
.vr-p i{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(10,12,18,.62);color:#fff;font:500 .66rem 'DM Mono',monospace;font-style:normal;pointer-events:none;}}
.vr-p i.m2{{display:none;}}
#gallery{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.94);z-index:9999;align-items:center;justify-content:center;}}
#gallery.open{{display:flex;}}
#gallery img{{max-width:92vw;max-height:84vh;object-fit:contain;border-radius:6px;}}
.gal-nav,.gal-close{{position:absolute;background:none;border:none;color:#fff;cursor:pointer;opacity:.55;}}
.gal-nav{{top:50%;transform:translateY(-50%);font-size:2.2rem;padding:18px 22px;}}
.gal-nav:hover,.gal-close:hover{{opacity:1;}}
#gal-prev{{left:0;}} #gal-next{{right:0;}}
.gal-close{{top:16px;right:22px;font-size:1.5rem;}}
.gal-cap{{position:absolute;bottom:22px;left:0;right:0;text-align:center;font-size:.78rem;color:rgba(255,255,255,.7);padding:0 60px;}}
.rf{{font-family:'DM Mono',monospace;font-size:.5rem;text-transform:uppercase;letter-spacing:.1em;color:var(--rose);border:1px solid var(--rose);border-radius:4px;padding:1px 5px;margin-left:8px;}}
.ext{{color:var(--teal);text-decoration:none;text-transform:none;letter-spacing:.04em;}}
.ext:hover{{color:var(--gold);}}
.note{{color:var(--muted);font-size:.85rem;margin-top:24px;}}
@media(max-width:600px){{.wrap{{padding:78px 16px 90px;}}.log{{padding:2px 12px 12px;}}.vr{{grid-template-columns:52px 1fr auto;gap:0 10px;}}.vr-x{{font-size:.84rem;}}.vr-p span{{width:42px;height:32px;}}.vr-p span:nth-child(n+3){{display:none;}}.vr-p i.m2{{display:flex;}}.yr-strip{{display:none;}}}}
</style>
</head>
<body>
<a class="crumb" href="/index.html">← dashboard</a>
<div class="wrap" id="app"></div>
<div id="gallery" onclick="if(event.target===this)galClose()">
  <img id="gal-img" src="" alt="">
  <button class="gal-nav" id="gal-prev" aria-label="Previous" onclick="event.stopPropagation();galStep(-1)">&#8592;</button>
  <button class="gal-nav" id="gal-next" aria-label="Next" onclick="event.stopPropagation();galStep(1)">&#8594;</button>
  <button class="gal-close" aria-label="Close" onclick="galClose()">&#10005;</button>
  <div class="gal-cap" id="gal-cap"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const VER = {ver_json};
const PIX = {pix_json};
const TOP = {top_json};
const app = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const fmt = n => Number(n).toLocaleString('en-US');
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
// weekday of a local 'YYYY-MM-DD HH:MM' string (built from its parts, so no timezone shift)
const wd = s => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)).getDay()];
// Mirrors metrics.venues.venue_shard (32-bit FNV-1a mod 256).
// ♥ 4 · Iennifer, Bojan Bačević, you + 1 other — friends by name; your own like
// as "you"; people outside your friends are only counted by the API.
function likeLine(l) {{
  const [n, names, me] = l, named = names ? names.split(', ').length : 0;
  const rest = Math.max(0, n - named - (me ? 1 : 0));
  const parts = names ? [esc(names)] : [];
  if (me) parts.push('you');
  let s = parts.join(', ');
  if (rest) s += (s ? ' <i>+ ' : '<i>') + rest + ' other' + (rest === 1 ? '' : 's') + '</i>';
  return '<span class="lk">♥ ' + n + (s ? ' · ' + s : '') + '</span>';
}}

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
let map = null, GAL = [], galIdx = 0;
function galShow() {{
  const g = GAL[galIdx]; if (!g) return;
  document.getElementById('gal-img').src = g.src;
  document.getElementById('gal-cap').textContent = (galIdx + 1) + ' / ' + GAL.length + ' · ' + g.cap;
}}
function galOpen(k) {{ if (k < 0) return; galIdx = k; galShow(); document.getElementById('gallery').classList.add('open'); }}
function galClose() {{ document.getElementById('gallery').classList.remove('open'); document.getElementById('gal-img').src = ''; }}
function galStep(d) {{ if (GAL.length) {{ galIdx = (galIdx + d + GAL.length) % GAL.length; galShow(); }} }}
document.addEventListener('keydown', e => {{
  if (!document.getElementById('gallery').classList.contains('open')) return;
  if (e.key === 'ArrowLeft') galStep(-1); else if (e.key === 'ArrowRight') galStep(1); else if (e.key === 'Escape') galClose();
}});
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
  // every photo of the venue, newest first; each thumbnail opens the shared lightbox at its index
  GAL = [];
  rec.v.forEach(v => (v[ix.photos] || []).forEach(p => GAL.push({{ src: PIX + '/' + p, cap: v[ix.local] + (v[ix.shout] ? ' · ' + v[ix.shout] : '') }})));
  if (GAL.length && PIX) h += '<section><div class="sec-h">Photos · ' + fmt(GAL.length) + '</div><div class="pgrid">' +
    GAL.slice(0, 48).map((g, k) => '<img loading="lazy" src="' + esc(pixT(g.src)) + '" alt="" onclick="galOpen(' + k + ')">').join('') + '</div>' +
    (GAL.length > 48 ? '<p class="note" style="margin-top:8px">All ' + fmt(GAL.length) + ' photos are in the visit history below.</p>' : '') + '</section>';
  // the whole visit history, grouped by year; the two most recent years start open
  const byYear = {{}};
  rec.v.forEach(v => (byYear[v[ix.local].slice(0, 4)] = byYear[v[ix.local].slice(0, 4)] || []).push(v));
  const years = Object.keys(byYear).sort().reverse();
  h += '<section class="hist"><div class="sec-h">Visit history · ' + fmt(rec.n) + (rec.n === 1 ? ' visit' : ' visits') + '</div>' + years.map((y, yi) => {{
    const vs = byYear[y], months = Array(12).fill(0);
    vs.forEach(v => months[+v[ix.local].slice(5, 7) - 1]++);
    const mm = Math.max(1, ...months), pics = vs.reduce((s, v) => s + (v[ix.photos] || []).length, 0);
    // a log: one row per visit — weekday + day with the time under it, the shout and companions,
    // up to 3 photo thumbnails on the right (the last one shows +N when there are more); a rule per month
    const byMon = {{}};
    vs.forEach(v => (byMon[v[ix.local].slice(5, 7)] = byMon[v[ix.local].slice(5, 7)] || []).push(v));
    const body = '<div class="log">' + Object.keys(byMon).sort().reverse().map(mo => {{
      const mv = byMon[mo];
      return '<div class="mh">' + MONTHS[+mo - 1] + '<span>' + mv.length + '</span></div>' + mv.map(v => {{
        const ph = (v[ix.photos] || []).length && PIX ? v[ix.photos] : [];
        const w = v[ix.with].length ? '<span class="w">with ' + v[ix.with].map(n => '<b>' + esc(n) + '</b>').join(', ') + '</span>' : '';
        const d = wd(v[ix.local]);
        // narrow screens show 2 thumbnails (CSS), so they get their own +N on the 2nd one
        const pics = ph.slice(0, 3).map((p, i) => {{
          const k = GAL.findIndex(g => g.src === PIX + '/' + p);
          return '<span onclick="galOpen(' + k + ')"><img loading="lazy" src="' + esc(PIX + '/t/' + p) + '" alt="">' +
            (i === 2 && ph.length > 3 ? '<i>+' + (ph.length - 3) + '</i>' : '') +
            (i === 1 && ph.length > 2 ? '<i class="m2">+' + (ph.length - 2) + '</i>' : '') + '</span>';
        }}).join('');
        return '<div class="vr' + (v[ix.rf] ? ' rfv' : '') + '"><div class="vr-d' + (d === 'Sat' || d === 'Sun' ? ' we' : '') + '"><div class="vr-day">' + d + '<b>' + +v[ix.local].slice(8, 10) + '</b></div>' +
          '<div class="vr-t">' + esc(v[ix.local].slice(11)) + '</div></div>' +
          '<div class="vr-x">' + (v[ix.shout] ? '«' + esc(v[ix.shout]) + '»' : '') + w + (v[ix.likes] ? likeLine(v[ix.likes]) : '') + (v[ix.rf] ? '<span class="rf">↺ reconstructed</span>' : '') + '</div>' +
          '<div class="vr-p">' + pics + '</div></div>';
      }}).join('');
    }}).join('') + '</div>';
    return '<details class="yr"' + (yi < 2 ? ' open' : '') + '><summary><span class="yr-y">' + y + '</span><span class="yr-meta">' +
      fmt(vs.length) + (vs.length === 1 ? ' visit' : ' visits') + (pics ? ' · ' + pics + ' photos' : '') + '</span>' +
      '<span class="yr-strip" title="visits per month">' + months.map((n, m) => '<i style="height:' + Math.round(18 * n / mm) + 'px" title="' + MONTHS[m] + ': ' + n + '"></i>').join('') + '</span>' +
      '<span class="yr-arrow">›</span></summary>' + body + '</details>';
  }}).join('') + '</section>';
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
    likes_by_checkin: dict | None = None,
    **_extra,
) -> int:
    """Write venue.html + venue_data_<xx>.json shards. Returns the venue count."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    venues = compute_venues(rows or [], photos_by_checkin, likes_by_checkin)
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
