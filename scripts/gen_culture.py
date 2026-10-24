# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_culture.py — culture.html: wine map, films, wines and sound on the check-in timeline.

Writes ``culture.html`` (static shell) + ``culture_data.json`` (`metrics.culture`
output plus the per-year Last.fm aggregate), fetched on load and cache-busted
with a content hash. The trip modal on trips.html reads the same JSON's
``trips`` block. Self-contained like the country pages; the only post-processed
placeholder is {{CARTO_KEY}}.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SITE_URL = "https://4sq.pages.dev"

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Culture · Foursquare history</title>
<meta name="description" content="Sound, taste and frame: Last.fm scrobbles, Vivino wines and IMDb films on the check-in timeline.">
<link rel="canonical" href="{site}/culture.html">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;700;900&family=DM+Sans:wght@400;500;600;700&family=DM+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);text-decoration:none;}} a:hover{{color:var(--gold);}}
.crumb{{position:fixed;top:14px;left:18px;z-index:1200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;}}
.wrap{{max-width:1240px;margin:0 auto;padding:88px 28px 120px;}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,6vw,4rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.profiles{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;}}
.profiles a{{font-family:'DM Mono',monospace;font-size:.62rem;letter-spacing:.08em;border:1px solid var(--border);border-radius:999px;padding:4px 12px;color:var(--muted);}}
.profiles a:hover{{border-color:var(--gold);color:var(--gold);}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:30px 0 8px;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px 16px 14px;}}
.kpi-num{{font-family:'Playfair Display',serif;font-size:1.9rem;font-weight:900;line-height:1.05;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.kpi:nth-child(3n+2) .kpi-num{{background:linear-gradient(135deg,#4cb8ff,#9b7bff);-webkit-background-clip:text;background-clip:text;}}
.kpi:nth-child(3n) .kpi-num{{background:linear-gradient(135deg,#2fd6b5,#4cb8ff);-webkit-background-clip:text;background-clip:text;}}
.kpi-lbl{{font-family:'DM Mono',monospace;font-size:.54rem;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin-top:6px;}}
section{{margin-top:50px;}}
.sec-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.2em;color:var(--gold);margin-bottom:14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;}}
.sec-h em{{color:var(--muted);font-style:normal;text-transform:none;letter-spacing:.04em;}}
#wmap{{height:440px;border-radius:14px;border:1px solid var(--border);background:var(--card2);}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:.74rem;color:var(--muted);margin-top:10px;}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px;}}
.tabs{{display:flex;gap:6px;flex-wrap:wrap;}}
.tabs button{{background:var(--card2);color:var(--muted);border:1px solid var(--border);border-radius:8px;padding:5px 11px;font-family:'DM Mono',monospace;font-size:.66rem;cursor:pointer;}}
.tabs button.on,.tabs button:hover{{color:var(--gold);border-color:var(--gold);}}
.list{{display:flex;flex-direction:column;}}
.item{{display:grid;grid-template-columns:92px 1fr auto;gap:12px;align-items:baseline;padding:9px 4px;border-bottom:1px solid var(--border);}}
.item .d{{font-family:'DM Mono',monospace;font-size:.64rem;color:var(--muted);}}
.item .t{{font-size:.9rem;overflow-wrap:anywhere;}}
.item .s{{display:block;font-size:.74rem;color:var(--muted);margin-top:2px;}}
.item .r{{font-family:'DM Mono',monospace;font-size:.8rem;color:var(--gold);white-space:nowrap;}}
.pill{{font-family:'DM Mono',monospace;font-size:.52rem;text-transform:uppercase;letter-spacing:.1em;border:1px solid var(--border);border-radius:4px;padding:1px 5px;margin-left:6px;color:var(--muted);}}
.pill.cinema{{color:var(--gold);border-color:var(--gold);}} .pill.road{{color:var(--rose);border-color:var(--rose);}}
.more{{margin-top:10px;background:none;border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:6px 14px;font-family:'DM Mono',monospace;font-size:.66rem;cursor:pointer;}}
.more:hover{{color:var(--gold);border-color:var(--gold);}}
.yr .a{{font-size:.86rem;margin-top:2px;}} .yr .n{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);}}
.note{{color:var(--muted);font-size:.82rem;margin-top:10px;max-width:780px;}}
.big-h{{font-family:'Playfair Display',serif;font-weight:700;font-size:clamp(1.6rem,3vw,2.3rem);letter-spacing:-.01em;margin-bottom:16px;display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap;}}
.big-h em{{font-style:normal;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.big-h small{{font-family:'DM Mono',monospace;font-size:.6rem;font-weight:400;color:var(--muted);background:var(--card2);padding:4px 10px;border-radius:999px;letter-spacing:.04em;}}
.wall{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;}}
.wl{{position:relative;display:block;aspect-ratio:3/4;border-radius:18px;overflow:hidden;background:linear-gradient(160deg,#241a2e,#141824);color:#fff;}}
.wl img{{position:absolute;inset:7% 14% 30%;width:72%;height:63%;object-fit:contain;filter:drop-shadow(0 12px 18px rgba(0,0,0,.5));transition:transform .4s;}}
.wl:hover img{{transform:scale(1.06) rotate(-2deg);}}
.wr{{position:absolute;top:10px;right:10px;font-family:'DM Mono',monospace;font-size:.62rem;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);color:#1a1206;padding:3px 8px;border-radius:999px;}}
.wn{{position:absolute;left:0;right:0;bottom:0;padding:22px 12px 10px;font-size:.78rem;font-weight:600;line-height:1.25;color:#fff;background:linear-gradient(transparent,rgba(9,11,16,.94) 40%);}}
.wn small{{display:block;font-family:'DM Mono',monospace;font-weight:400;font-size:.56rem;color:var(--muted);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.wn span{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}}
.films{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;}}
.ft{{position:relative;display:block;border-radius:18px;padding:16px;min-height:140px;background:linear-gradient(160deg,color-mix(in srgb,var(--c) 30%,#12141c),#12141c 72%);border:1px solid color-mix(in srgb,var(--c) 30%,transparent);color:var(--text);overflow:hidden;transition:transform .2s;}}
.ft:hover{{transform:translateY(-3px);color:var(--text);}}
.fr{{font-family:'Playfair Display',serif;font-weight:900;font-size:2.4rem;line-height:1;color:var(--c);}}.fr small{{font-size:.9rem;color:var(--muted);}}
.fn{{font-weight:700;font-size:.98rem;margin-top:10px;line-height:1.2;}}.fm{{font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);margin-top:4px;}}
.fw{{position:absolute;top:14px;right:14px;font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.12em;text-transform:uppercase;color:var(--c);}}
.years{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;}}
.yr{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px;}}
.yr b{{font-family:'Playfair Display',serif;font-size:1.8rem;font-weight:900;background:linear-gradient(135deg,#4cb8ff,#9b7bff);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.yr .cnt{{display:flex;gap:10px;font-family:'DM Mono',monospace;font-size:.62rem;color:var(--text);margin:4px 0 10px;}}
.yr .a{{font-weight:700;font-size:.95rem;}}
@media(max-width:900px){{.wall{{grid-template-columns:repeat(3,minmax(0,1fr));}}.films{{grid-template-columns:repeat(2,minmax(0,1fr));}}}}
@media(max-width:600px){{.wall{{grid-template-columns:repeat(2,minmax(0,1fr));}}.ft{{min-height:120px;}}.fr{{font-size:2rem;}}.wrap{{padding:78px 14px 90px;}}.item{{grid-template-columns:70px 1fr auto;gap:8px;}}#wmap{{height:340px;}}}}
</style>
</head>
<body>
<a class="crumb" href="/index.html">← dashboard</a>
<div class="wrap">
<h1 class="hero-name">Sound · Taste · Frame</h1>
<div class="hero-sub">Last.fm, Vivino and IMDb on the check-in timeline</div>
<div class="profiles">{profiles}</div>
<div class="kpis" id="kpis"></div>
<section><div class="big-h"><span>The wine <em>wall</em></span><small>latest 18 · tap for Vivino</small></div><div class="wall" id="wall"></div></section>
<section><div class="big-h"><span>Recently <em>watched</em></span><small>colour = first genre · number = your rating</small></div><div class="films" id="ftiles"></div></section>
<section><div class="sec-h">Wine map <em>· each rated bottle at the check-in nearest its scan</em></div><div id="wmap"></div><div class="legend" id="wlegend"></div></section>
<section><div class="big-h"><span>Year by <em>year</em></span><small>films · wines · most-played artist</small></div><div class="years" id="sound"></div></section>
<section><div class="sec-h">All films <div class="tabs" id="ftabs"></div></div><div class="list" id="flist"></div><button class="more" id="fmore" hidden>Show more</button>
<p class="note">IMDb keeps the rating date, not the viewing date. A film counts as seen in the cinema when it was rated on the day of a movie-theatre check-in or up to two days later and is a new release; on the road when the day falls inside a trip. Days with ten or more ratings are catch-up sessions (backlog).</p></section>
<section><div class="sec-h">All wines <div class="tabs" id="wtabs"></div></div><div class="list" id="wlist"></div><button class="more" id="wmore" hidden>Show more</button></section>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const VER = {ver_json};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const fmt = n => Number(n).toLocaleString('en-US');
const WCOL = {{'Red Wine':'#c0485a','White Wine':'#e8d48a','Sparkling':'#4ec9b0','Rosé Wine':'#f0a0b4','Dessert Wine':'#d08a3a','Fortified Wine':'#9a6a3a'}};
const PAGE = 60;
const GCOL = {{Action:'#ff7a6b',Comedy:'#f6c25b',Drama:'#9b7bff',Animation:'#2fd6b5','Sci-Fi':'#4cb8ff',Horror:'#ff6fae',Thriller:'#c084fc',Crime:'#fb923c',Adventure:'#34d399',Romance:'#f472b6',Documentary:'#b8e65c'}};
function venueLink(o) {{ return o.venue ? (o.venue_id ? '<a href="/venue.html#' + esc(o.venue_id) + '">' + esc(o.venue) + '</a>' : esc(o.venue)) : ''; }}
function tripLink(o) {{ return o.trip != null ? '<a href="/trips.html#trip-' + o.trip + '">' + esc(o.trip_name || 'trip') + '</a>' : ''; }}
function pager(listEl, moreEl, items, row) {{
  let shown = 0;
  function more() {{ listEl.insertAdjacentHTML('beforeend', items.slice(shown, shown + PAGE).map(row).join('')); shown += PAGE; moreEl.hidden = shown >= items.length; }}
  listEl.innerHTML = ''; moreEl.onclick = more; more();
}}
function tabs(el, opts, onPick) {{
  el.innerHTML = opts.map(([k, l], i) => '<button data-k="' + k + '"' + (i ? '' : ' class="on"') + '>' + esc(l) + '</button>').join('');
  el.querySelectorAll('button').forEach(b => b.onclick = () => {{ el.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); onPick(b.dataset.k); }});
  onPick(opts[0][0]);
}}
function render(D) {{
  const films = D.films.slice().reverse(), wines = D.wines.slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const seen = films.filter(f => f.where !== 'backlog');
  const cnt = k => films.filter(f => f.where === k).length;
  const avg = (xs, k) => xs.length ? (xs.reduce((s, x) => s + x[k], 0) / xs.length).toFixed(1) : '—';
  const scrob = Object.values(D.music || {{}}).reduce((s, y) => s + (y.scrobbles || 0), 0);
  document.getElementById('kpis').innerHTML = [
    [fmt(scrob), 'scrobbles'], [fmt(films.length), 'films rated'], [cnt('cinema'), 'in the cinema'],
    [avg(seen, 'rating'), 'avg film rating /10'], [fmt(wines.length), 'wines rated'], [avg(wines, 'rating'), 'avg wine rating /5'],
    [new Set(wines.map(w => w.country).filter(Boolean)).size, 'wine countries']
  ].map(([a, b]) => '<div class="kpi"><div class="kpi-num">' + esc(a) + '</div><div class="kpi-lbl">' + esc(b) + '</div></div>').join('');

  // wine map: one marker per place, every bottle in the popup
  const placed = wines.filter(w => w.lat != null && w.lng != null), groups = {{}};
  placed.forEach(w => {{ const k = w.lat.toFixed(4) + ',' + w.lng.toFixed(4); (groups[k] = groups[k] || []).push(w); }});
  const map = L.map('wmap', {{ scrollWheelZoom: false, worldCopyJump: true }});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={{{{CARTO_KEY}}}}', {{ attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 18 }}).addTo(map);
  const pts = [];
  Object.values(groups).forEach(g => {{
    const top = g.slice().sort((a, b) => b.rating - a.rating)[0];
    pts.push([g[0].lat, g[0].lng]);
    L.circleMarker([g[0].lat, g[0].lng], {{ radius: Math.min(16, 4 + 2 * Math.sqrt(g.length)), color: WCOL[top.type] || '#e8b86d', fillColor: WCOL[top.type] || '#e8b86d', fillOpacity: .75, weight: 1 }})
      .bindPopup('<b>' + venueLink(g[0]) + '</b> · ' + esc(g[0].city) + '<br>' + g.slice(0, 12).map(w => esc(w.date) + ' · <a href="' + esc(w.url) + '" target="_blank" rel="noopener">' + esc(w.wine) + '</a> ' + esc(w.vintage) + ' — ' + w.rating).join('<br>') + (g.length > 12 ? '<br>+' + (g.length - 12) + ' more' : ''))
      .addTo(map);
  }});
  if (pts.length) map.fitBounds(pts, {{ padding: [20, 20], maxZoom: 6 }}); else map.setView([50, 20], 4);
  const types = {{}}; wines.forEach(w => types[w.type] = (types[w.type] || 0) + 1);
  document.getElementById('wlegend').innerHTML = Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, n]) => '<span><i style="background:' + (WCOL[t] || '#e8b86d') + '"></i>' + esc(t) + ' · ' + n + '</span>').join('') +
    '<span>' + (wines.length - placed.length) + ' without a place (no check-in near the scan)</span>';

  // the wall: the newest bottles that have a label photo
  document.getElementById('wall').innerHTML = wines.filter(w => w.label).slice(0, 18).map(w =>
    '<a class="wl" href="' + esc(w.url) + '" target="_blank" rel="noopener" title="' + esc(w.wine + ' ' + (w.vintage || '')) + '"><img loading="lazy" src="' + esc(w.label) + '" alt=""><span class="wr">★ ' + w.rating + '</span>' +
    '<div class="wn"><span>' + esc(w.wine) + '</span><small>' + esc([w.winery, w.country, w.vintage].filter(Boolean).join(' · ')) + '</small></div></a>').join('');
  // film tiles: the latest films actually watched (backlog catch-up days left out)
  document.getElementById('ftiles').innerHTML = seen.slice(0, 12).map(f => {{
    const c = GCOL[(f.genres || [])[0]] || '#9b7bff';
    return '<a class="ft" style="--c:' + c + '" href="' + esc(f.url) + '" target="_blank" rel="noopener"><div class="fr">' + f.rating + '<small>/10</small></div>' +
      '<div class="fn">' + esc(f.title) + '</div><div class="fm">' + esc([f.year, (f.genres || []).slice(0, 2).join(', ')].filter(Boolean).join(' · ')) + '</div><div class="fw">' + esc(f.where) + '</div></a>';
  }}).join('');

  // films
  const frow = f => '<div class="item"><span class="d">' + esc(f.date) + '</span><span class="t"><a href="' + esc(f.url) + '" target="_blank" rel="noopener">' + esc(f.title) + '</a> <span style="color:var(--muted)">' + esc(f.year || '') + '</span>' +
    '<span class="pill ' + f.where + '">' + f.where + '</span><span class="s">' + [venueLink(f), tripLink(f), esc((f.genres || []).slice(0, 3).join(', '))].filter(Boolean).join(' · ') + '</span></span><span class="r">' + f.rating + '/10</span></div>';
  tabs(document.getElementById('ftabs'), [['all', 'All ' + films.length], ['cinema', 'Cinema ' + cnt('cinema')], ['road', 'On the road ' + cnt('road')], ['home', 'At home ' + cnt('home')], ['backlog', 'Backlog ' + cnt('backlog')], ['best', 'Best rated']],
    k => pager(document.getElementById('flist'), document.getElementById('fmore'),
      k === 'all' ? films : k === 'best' ? seen.slice().sort((a, b) => b.rating - a.rating || (a.date < b.date ? 1 : -1)) : films.filter(f => f.where === k), frow));

  // wines
  const wrow = w => '<div class="item"><span class="d">' + esc(w.date || '—') + '</span><span class="t"><a href="' + esc(w.url) + '" target="_blank" rel="noopener">' + esc(w.wine) + '</a> <span style="color:var(--muted)">' + esc(w.vintage) + '</span>' +
    '<span class="s">' + [esc(w.winery), esc([w.region, w.country].filter(Boolean).join(', ')), esc(w.type), venueLink(w), tripLink(w)].filter(Boolean).join(' · ') + (w.review ? '<br>“' + esc(w.review) + '”' : '') + '</span></span><span class="r">★ ' + w.rating + '</span></div>';
  tabs(document.getElementById('wtabs'), [['recent', 'Recent'], ['best', 'Best rated'], ['trips', 'On trips']],
    k => pager(document.getElementById('wlist'), document.getElementById('wmore'),
      k === 'best' ? wines.slice().sort((a, b) => b.rating - a.rating) : k === 'trips' ? wines.filter(w => w.trip != null) : wines, wrow));

  // year by year: films and wines rated that year + the year in sound
  const fy = {{}}, wy = {{}};
  films.forEach(f => {{ const y = (f.date || '').slice(0, 4); if (y) fy[y] = (fy[y] || 0) + 1; }});
  wines.forEach(w => {{ const y = (w.date || '').slice(0, 4); if (y) wy[y] = (wy[y] || 0) + 1; }});
  const allY = [...new Set([...Object.keys(D.music || {{}}).filter(y => (D.music[y] || {{}}).scrobbles), ...Object.keys(fy), ...Object.keys(wy)])].filter(y => +y >= 2000).sort((a, b) => b - a);
  document.getElementById('sound').innerHTML = allY.map(y => {{
    const v = (D.music || {{}})[y] || {{}};
    const tt = ((D.years || {{}})[y] || {{}}).top_tracks || [];
    return '<div class="yr"><b>' + y + '</b><div class="cnt"><span>🎬 ' + (fy[y] || 0) + '</span><span>🍷 ' + (wy[y] || 0) + '</span>' + (v.scrobbles ? '<span>♪ ' + fmt(v.scrobbles) + '</span>' : '') + '</div>' +
      '<div class="a">' + esc((v.top_artist || {{}}).name || '—') + '</div>' + (v.scrobbles ? '<div class="n">' + fmt((v.top_artist || {{}}).plays || 0) + ' plays · top artist</div>' : '') +
      (tt.length ? '<div class="n" style="margin-top:6px">' + tt.slice(0, 3).map(t => esc(t[0] + ' — ' + t[1]) + ' ×' + t[2]).join('<br>') + '</div>' : '') + '</div>';
  }}).join('') || '<p class="note">No Last.fm data yet.</p>';
}}
fetch('culture_data.json?v=' + VER).then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }}).then(render)
  .catch(e => {{ document.getElementById('kpis').innerHTML = '<p class="note">Could not load culture_data.json (' + esc(e.message) + ').</p>'; }});
</script>
</body>
</html>"""


def build_page(out_path: str = "", culture: dict | None = None, music_by_year: dict | None = None,
               profiles: dict | None = None, **_extra) -> int:
    """Write culture.html + culture_data.json. Returns films + wines placed on the page."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = dict(culture or {"films": [], "wines": [], "trips": {}, "years": {}})
    data["music"] = music_by_year or {}
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "culture_data.json").write_text(body, encoding="utf-8")
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    links = [(lbl, (profiles or {}).get(key)) for lbl, key in
             (("Last.fm ↗", "lastfm_profile"), ("Vivino ↗", "vivino_profile"), ("IMDb ↗", "imdb_profile"))]
    prof_html = "".join(f'<a href="{_attr(u)}" target="_blank" rel="noopener">{lbl}</a>' for lbl, u in links if u)
    (out_dir / "culture.html").write_text(
        PAGE_TEMPLATE.format(site=SITE_URL, ver_json=json.dumps(ver), profiles=prof_html), encoding="utf-8")
    log.info("culture.html: %d films, %d wines, %d trips with culture",
             len(data["films"]), len(data["wines"]), len(data.get("trips", {})))
    return len(data["films"]) + len(data["wines"])


def _attr(s: str) -> str:
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
