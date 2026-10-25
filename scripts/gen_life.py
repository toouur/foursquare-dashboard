# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_life.py — "Life in stripes": the whole history as parallel stripes on one time axis.

Writes ``life.html`` (static shell) + ``life_data.json`` (from `metrics.life`, fetched
on load, cache-busted with a content hash). Each band is an SVG whose viewBox is
one unit per day, so the bands line up exactly; labels are HTML overlays.
Self-contained like the country pages.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from metrics.life import compute_life

log = logging.getLogger(__name__)

SITE_URL = "https://4sq.pages.dev"

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Life in Stripes · Foursquare history</title>
<meta name="description" content="Twenty years of check-ins as parallel stripes: home, country, trips, rhythm, people, music.">
<link rel="canonical" href="{site}/life.html">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;700;900&family=DM+Sans:wght@400;500;600;700&family=DM+Mono&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#2fd6b5;--coral:#ff7a6b;--violet:#9b7bff;--sky:#4cb8ff;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);}}
.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}
.wrap{{max-width:1320px;margin:0 auto;padding:88px 22px 120px;}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,6vw,4rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(135deg,#f6d38b 0%,#ff9a6b 55%,#ff6fae 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:26px 0 0;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:14px 16px;}}
.kpi b{{display:block;font-family:'Playfair Display',serif;font-size:1.7rem;font-weight:900;line-height:1;background:linear-gradient(135deg,#f6d38b,#ff9a6b 55%,#ff6fae);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
.kpi:nth-child(2) b{{background:linear-gradient(135deg,#4cb8ff,#9b7bff);-webkit-background-clip:text;background-clip:text;}}
.kpi:nth-child(3) b{{background:linear-gradient(135deg,#2fd6b5,#4cb8ff);-webkit-background-clip:text;background-clip:text;}}
.kpi span{{display:block;font-family:'DM Mono',monospace;font-size:.54rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-top:7px;}}
.tools{{display:flex;gap:6px;align-items:center;margin:22px 0 10px;flex-wrap:wrap;}}
.tools .lbl{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);margin-right:2px;}}
.tools button{{background:var(--card2);color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:5px 12px;font-family:'DM Mono',monospace;font-size:.68rem;cursor:pointer;}}
.tools button.on,.tools button:hover{{color:#1a1206;background:linear-gradient(135deg,#f6d38b,#ff9a6b);border-color:transparent;}}
.tools .key{{margin-left:auto;display:flex;gap:12px;font-family:'DM Mono',monospace;font-size:.58rem;color:var(--muted);}}
.tools .key i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px;}}
.chart{{display:grid;grid-template-columns:170px minmax(0,1fr);background:var(--card);border:1px solid var(--border);border-radius:18px;overflow:hidden;}}
.labels{{border-right:1px solid var(--border);}}
.labels div,.lane{{display:flex;align-items:center;border-bottom:1px solid var(--border);}}
.labels div{{font-family:'DM Mono',monospace;font-size:.56rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);padding:0 12px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
.labels div.sub{{text-transform:none;letter-spacing:0;font-family:'DM Sans',sans-serif;font-size:.74rem;font-weight:500;color:var(--text);}}
.labels div.sub i{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:8px;flex-shrink:0;}}
.labels .nm{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.scroll{{overflow-x:auto;overflow-y:hidden;position:relative;scrollbar-width:thin;}}
.inner{{position:relative;min-width:100%;}}
.lane{{position:relative;overflow:hidden;}}
.lane svg{{display:block;width:100%;}}
.lane.alt{{background:rgba(255,255,255,.015);}}
.ticks{{height:26px;position:relative;border-bottom:1px solid var(--border);}}
.ticks span{{position:absolute;top:6px;font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);transform:translateX(5px);white-space:nowrap;}}
.ylines{{position:absolute;top:26px;bottom:0;left:0;right:0;pointer-events:none;}}
.ylines i{{position:absolute;top:0;bottom:0;width:1px;background:rgba(255,255,255,.055);}}
.ov{{position:absolute;top:0;bottom:0;display:flex;align-items:center;overflow:hidden;white-space:nowrap;font-size:.68rem;padding:0 8px;color:#0a0c12;font-weight:700;}}
.music .mu{{position:absolute;height:50%;display:flex;align-items:center;white-space:nowrap;overflow:hidden;font-size:.64rem;padding:0 6px;color:var(--text);border-left:2px solid var(--violet);}}
.music .mu b{{font-weight:400;color:var(--muted);margin-left:5px;font-family:'DM Mono',monospace;font-size:.54rem;}}
.trip-link rect{{cursor:pointer;transition:opacity .15s;}}
.trip-link:hover rect{{fill:#fff;}}
.cursor{{position:absolute;top:26px;bottom:0;width:1px;background:rgba(255,255,255,.65);pointer-events:none;display:none;box-shadow:0 0 8px rgba(255,255,255,.4);}}
.tip{{position:fixed;z-index:300;pointer-events:none;display:none;max-width:300px;background:rgba(14,16,24,.96);backdrop-filter:blur(10px);border:1px solid var(--border);border-radius:12px;padding:10px 12px;font-size:.74rem;line-height:1.45;box-shadow:0 14px 40px rgba(0,0,0,.5);}}
.tip .d{{font-family:'DM Mono',monospace;font-size:.6rem;color:var(--gold);letter-spacing:.08em;margin-bottom:4px;}}
.tip .r{{display:flex;gap:8px;}}.tip .r span:first-child{{font-family:'DM Mono',monospace;font-size:.54rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;min-width:52px;padding-top:2px;}}
.tip i{{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;}}
.legend{{display:flex;flex-wrap:wrap;gap:6px;margin-top:16px;}}
.legend button{{display:inline-flex;align-items:center;gap:6px;font-family:'DM Mono',monospace;font-size:.62rem;padding:4px 10px;border-radius:999px;background:var(--card2);border:1px solid transparent;color:var(--text);cursor:pointer;}}
.legend button i{{width:9px;height:9px;border-radius:2px;}}
.legend button.on{{border-color:var(--gold);}}
.legend button b{{font-weight:400;color:var(--muted);}}
.note{{color:var(--muted);font-size:.85rem;margin-top:18px;max-width:780px;}}
@media(max-width:700px){{.wrap{{padding:78px 12px 90px;}}.chart{{grid-template-columns:92px minmax(0,1fr);}}.labels div{{padding:0 8px;font-size:.5rem;}}.labels div.sub{{font-size:.66rem;}}.tools .key{{margin-left:0;width:100%;}}.kpi b{{font-size:1.4rem;}}}}
</style>
</head>
<body>
<a class="crumb" href="/stats.html">← stats</a>
<div class="wrap">
<h1 class="hero-name">Life in Stripes</h1>
<div class="hero-sub" id="sub">life in stripes</div>
<div class="kpis" id="kpis"></div>
<div class="tools"><span class="lbl">zoom</span>
<button data-z="1">1×</button><button data-z="2">2×</button><button data-z="4">4×</button><button data-z="10">10×</button>
<div class="key"><span><i style="background:var(--teal)"></i>trip on the ground</span><span><i style="background:var(--coral)"></i>trip with a flight</span></div></div>
<div class="chart"><div class="labels" id="labels"></div><div class="scroll" id="scroll"><div class="inner" id="inner"></div></div></div>
<div class="legend" id="legend"></div>
<p class="note">Hover anywhere for that day; click a trip to open it; click a country below to light up only its days. Country: the day's most-checked-in country — a day without check-ins keeps the previous one and is drawn faded; the most-lived countries get quiet colours so travel stands out. Density is check-ins per week; people rows show check-ins together per month.</p>
</div>
<div class="tip" id="tip"></div>
<script>
const VER = {ver_json};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const NS = 'http://www.w3.org/2000/svg';
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], WD = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const PEOPLE_COL = ['#2fd6b5','#4cb8ff','#9b7bff','#ff6fae','#ff7a6b','#f6c25b','#b8e65c','#5ee0f0'];
let L = null, DAYS = 0, T0 = 0, COLORS = [], HL = null, ZOOM = 1;
const dayStr = i => new Date(T0 + i * 864e5).toISOString().slice(0, 10);
const dayNice = i => {{ const d = new Date(T0 + i * 864e5); return WD[d.getUTCDay()] + ' ' + d.getUTCDate() + ' ' + MON[d.getUTCMonth()] + ' ' + d.getUTCFullYear(); }};
const isAir = t => String(t[2]).includes('✈');
// phones: "Marina Gandacova" → "Marina G." so the label column stays narrow
const shortName = s => {{ const p = String(s).trim().split(/\s+/); return p.length > 1 && p[1].length > 2 ? p[0] + ' ' + [...p[1]][0] + '.' : s; }};
function svg(h, parts) {{ return '<svg xmlns="' + NS + '" viewBox="0 0 ' + DAYS + ' ' + h + '" preserveAspectRatio="none" style="height:' + h + 'px">' + parts.join('') + '</svg>'; }}
function rect(x, w, y, h, fill, extra) {{ return '<rect x="' + x + '" width="' + w + '" y="' + y + '" height="' + h + '" fill="' + fill + '"' + (extra || '') + '/>'; }}
function pct(i) {{ return (100 * i / DAYS) + '%'; }}
function find(runs, i) {{   // runs sorted by start: [start, len, …]
  let lo = 0, hi = runs.length - 1;
  while (lo <= hi) {{ const m = (lo + hi) >> 1, r = runs[m]; if (i < r[0]) hi = m - 1; else if (i >= r[0] + r[1]) lo = m + 1; else return r; }}
  return null;
}}
const yStart = y => (Date.parse(y + '-01-01T00:00:00Z') - T0) / 864e5;
function countryLane() {{
  return svg(40, L.country_runs.map(r => {{
    const dim = HL != null && r[2] !== HL;
    return rect(r[0], r[1], 0, 40, COLORS[r[2]], dim ? ' fill-opacity=".08"' : (r[3] ? ' fill-opacity=".45"' : ''));
  }}));
}}
function ticks() {{
  // thin the year labels to what fits at the current zoom
  const y0 = +L.start.slice(0, 4), y1 = +dayStr(DAYS - 1).slice(0, 4);
  const pxYear = document.getElementById('scroll').clientWidth * ZOOM / (DAYS / 365.25);
  const step = pxYear >= 42 ? 1 : pxYear >= 22 ? 2 : pxYear >= 11 ? 4 : 5;
  let t = '';
  for (let y = y0 + 1; y <= y1; y++) if (y % step === 0 || step === 1) t += '<span style="left:' + pct(yStart(y)) + '">' + (pxYear >= 42 ? y : "'" + String(y).slice(2)) + '</span>';
  document.getElementById('ticks').innerHTML = t;
}}
function build() {{
  T0 = Date.parse(L.start + 'T00:00:00Z'); DAYS = L.days;
  const cdays = L.countries.map(() => 0);
  L.country_runs.forEach(r => cdays[r[2]] += r[1]);
  const order = cdays.map((d, i) => [d, i]).sort((a, b) => b[0] - a[0]).map(p => p[1]);
  const quiet = ['#3a4058', '#4a4560'];
  const vivid = ['#9b7bff','#f6c25b','#4cb8ff','#ff6fae','#b8e65c','#5ee0f0','#ff7a6b','#c084fc','#34d399','#fb923c','#f472b6','#60a5fa'];
  order.forEach((ci, k) => {{ COLORS[ci] = k < 2 ? quiet[k] : (k - 2 < vivid.length ? vivid[k - 2] : 'hsl(' + Math.round((k * 137.508) % 360) + ',62%,62%)'); }});
  const lab = [], lanes = [];
  const add = (label, h, html, cls, dot) => {{
    // the name sits in its own span: text-overflow cannot ellipsize a flex container's text
    const shown = dot && innerWidth < 700 ? shortName(label) : label;
    lab.push('<div class="' + (cls || '') + '" style="height:' + h + 'px" title="' + esc(label) + '">' + (dot ? '<i style="background:' + dot + '"></i>' : '') + '<span class="nm">' + esc(shown) + '</span></div>');
    lanes.push('<div class="lane ' + (cls || '') + '" style="height:' + h + 'px">' + html + '</div>');
  }};
  lab.push('<div style="height:26px"></div>');
  lanes.push('<div class="ticks" id="ticks"></div>');
  const eraFill = {{}}; let ek = 0;
  add('home', 34, svg(34, L.eras.map(e => {{ if (!(e[2] in eraFill)) eraFill[e[2]] = ['#e8b86d', '#2fd6b5', '#ff9a6b'][ek++ % 3]; return rect(e[0], e[1], 0, 34, eraFill[e[2]]); }})) +
    L.eras.map(e => '<div class="ov" style="left:' + pct(e[0]) + ';width:' + pct(e[1]) + '" title="' + esc(e[2] + ' · ' + dayStr(e[0]).slice(0, 7) + ' → ' + dayStr(Math.min(DAYS - 1, e[0] + e[1] - 1)).slice(0, 7)) + '">' + esc(e[2]) + ' <span style="font-weight:400;opacity:.65;margin-left:6px;font-family:DM Mono,monospace;font-size:.56rem">' + dayStr(e[0]).slice(0, 4) + '–' + dayStr(Math.min(DAYS - 1, e[0] + e[1] - 1)).slice(0, 4) + '</span></div>').join(''));
  add('country', 40, '<div id="clane" style="width:100%">' + countryLane() + '</div>');
  // trips coloured by how you got there; each trip is a real link with its own hit area
  add('trips', 26, svg(26, L.trips.map(t => {{
    const r = '<rect x="' + t[0] + '" width="' + Math.max(t[1], 1) + '" y="4" height="18" rx="0" fill="' + (isAir(t) ? '#ff7a6b' : '#2fd6b5') + '"><title>' + esc(t[2]) + '</title></rect>';
    return t[3] != null ? '<a href="/trips.html#trip-' + t[3] + '" class="trip-link">' + r + '</a>' : r;
  }})));
  // density: an area under the weekly check-in count (square-root scale)
  const mx = Math.sqrt(Math.max(1, ...L.weeks)), H = 64;
  let path = 'M0,' + H;
  L.weeks.forEach((n, k) => {{ const y = (H - (H - 4) * Math.sqrt(n) / mx).toFixed(1); path += 'L' + (k * 7) + ',' + y + 'L' + (k * 7 + 7) + ',' + y; }});
  path += 'L' + DAYS + ',' + H + 'Z';
  add('density', H, '<svg xmlns="' + NS + '" viewBox="0 0 ' + DAYS + ' ' + H + '" preserveAspectRatio="none" style="height:' + H + 'px"><defs><linearGradient id="dg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff9a6b"/><stop offset="1" stop-color="#e8b86d" stop-opacity=".35"/></linearGradient></defs><path d="' + path + '" fill="url(#dg)"/></svg>');
  if (L.people.length) {{
    const mStart = L.months.map(m => (Date.parse(m + '-01T00:00:00Z') - T0) / 864e5);
    L.people.forEach(([name, cnt], pi) => {{
      const top = Math.max(1, ...cnt), col = PEOPLE_COL[pi % PEOPLE_COL.length];
      add(name, 20, svg(20, cnt.map((n, k) => n ? rect(Math.max(0, mStart[k]), (k + 1 < mStart.length ? mStart[k + 1] : DAYS) - Math.max(0, mStart[k]), 3, 14, col, ' fill-opacity="' + (0.18 + 0.82 * n / top).toFixed(2) + '"') : '')), 'sub' + (pi % 2 ? ' alt' : ''), col);
    }});
  }}
  if (L.music.length) {{
    // two staggered rows (odd / even years) so each name gets two years of room
    add('music', 44, L.music.map(([y, artist, plays], k) => {{
      const a = Math.max(0, yStart(y)), b = Math.min(DAYS, yStart(+y + 2));
      return '<div class="mu" style="top:' + (+y % 2 ? '50%' : '0') + ';left:' + pct(a) + ';width:' + pct(b - a) + '" title="' + esc(y + ': ' + artist + ' · ' + plays + ' plays') + '">' + esc(artist) + '<b>' + String(y).slice(2) + '</b></div>';
    }}).join(''), 'music');
  }}
  document.getElementById('labels').innerHTML = lab.join('');
  let yl = '';
  for (let y = +L.start.slice(0, 4) + 1; y <= +dayStr(DAYS - 1).slice(0, 4); y++) yl += '<i style="left:' + pct(yStart(y)) + '"></i>';
  document.getElementById('inner').innerHTML = lanes.join('') + '<div class="ylines">' + yl + '</div><div class="cursor" id="cursor"></div>';
  document.getElementById('legend').innerHTML = order.slice(0, 24).map(ci => '<button data-c="' + ci + '"><i style="background:' + COLORS[ci] + '"></i>' + esc(L.countries[ci]) + ' <b>' + cdays[ci].toLocaleString('en-US') + ' d</b></button>').join('');
  document.querySelectorAll('.legend button').forEach(b => b.onclick = () => {{
    const c = +b.dataset.c; HL = HL === c ? null : c;
    document.querySelectorAll('.legend button').forEach(x => x.classList.toggle('on', HL != null && +x.dataset.c === HL));
    document.getElementById('clane').innerHTML = countryLane();
  }});
  const abroad = L.country_runs.filter(r => order.indexOf(r[2]) >= 2 && !r[3]).reduce((s, r) => s + r[1], 0);
  document.getElementById('kpis').innerHTML = [[DAYS.toLocaleString('en-US'), 'days on the record'], [L.countries_total || L.countries.length, 'countries'], [L.trips.length, 'trips'],
    [abroad.toLocaleString('en-US'), 'days abroad'], [L.eras.length, 'home eras']].map(([a, b]) => '<div class="kpi"><b>' + a + '</b><span>' + b + '</span></div>').join('');
  document.getElementById('sub').textContent = L.start + ' → ' + dayStr(DAYS - 1);
  ticks();
}}
function tipHtml(i) {{
  const r = find(L.country_runs, i), e = find(L.eras, i), t = L.trips.filter(t => i >= t[0] && i < t[0] + t[1]);
  const ds = dayStr(i), m = L.months.indexOf(ds.slice(0, 7)), mu = L.music.find(x => x[0] === ds.slice(0, 4));
  const ppl = m < 0 ? [] : L.people.filter(p => p[1][m]).map(p => esc(p[0]) + ' ' + p[1][m]);
  const row = (k, v) => v ? '<div class="r"><span>' + k + '</span><span>' + v + '</span></div>' : '';
  return '<div class="d">' + dayNice(i) + '</div>' +
    row('home', e ? esc(e[2]) : '') +
    row('country', r ? '<i style="background:' + COLORS[r[2]] + '"></i>' + esc(L.countries[r[2]]) + (r[3] ? ' <span style="color:var(--muted)">(no check-in)</span>' : '') : '') +
    row('trip', t.map(x => esc(x[2])).join(', ')) +
    row('week', (L.weeks[Math.floor(i / 7)] || 0) + ' check-ins') +
    row('with', ppl.join(' · ')) +
    row('music', mu ? esc(mu[1]) + ' <span style="color:var(--muted)">top of ' + mu[0] + '</span>' : '');
}}
const inner = document.getElementById('inner'), tip = document.getElementById('tip');
inner.addEventListener('mousemove', ev => {{
  const box = inner.getBoundingClientRect(), i = Math.max(0, Math.min(DAYS - 1, Math.floor((ev.clientX - box.left) / box.width * DAYS)));
  const c = document.getElementById('cursor'); c.style.display = 'block'; c.style.left = pct(i);
  tip.innerHTML = tipHtml(i); tip.style.display = 'block';
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(innerWidth - w - 10, ev.clientX + 16) + 'px';
  tip.style.top = Math.min(innerHeight - h - 10, Math.max(10, ev.clientY - h / 2)) + 'px';
}});
inner.addEventListener('mouseleave', () => {{ const c = document.getElementById('cursor'); if (c) c.style.display = 'none'; tip.style.display = 'none'; }});
function setZoom(z) {{
  const sc = document.getElementById('scroll'), mid = inner.offsetWidth ? (sc.scrollLeft + sc.clientWidth / 2) / inner.offsetWidth : 1;
  ZOOM = z;
  document.querySelectorAll('.tools button').forEach(x => x.classList.toggle('on', +x.dataset.z === z));
  inner.style.width = (100 * z) + '%';
  sc.scrollLeft = mid * inner.offsetWidth - sc.clientWidth / 2;
  if (L) ticks();
}}
document.querySelectorAll('.tools button').forEach(b => b.onclick = () => setZoom(+b.dataset.z));
fetch('life_data.json?v=' + VER).then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
  .then(j => {{
    L = j; if (!L.days) throw new Error('no data');
    // phones start zoomed in at the latest years: 1× squeezes 19 years into ~270px
    build(); setZoom(innerWidth < 700 ? 4 : 1);
    requestAnimationFrame(() => {{ const sc = document.getElementById('scroll'); sc.scrollLeft = sc.scrollWidth; }});
  }})
  .catch(e => {{ document.getElementById('legend').innerHTML = '<p class="note">Could not load life_data.json (' + esc(e.message) + ').</p>'; }});
</script>
</body>
</html>"""


def build_page(
    out_path: str = "",
    rows: list | None = None,
    trips: list | None = None,
    home_city: str = "",
    home_history: list | None = None,
    music_by_year: dict | None = None,
    country_total: int = 0,
    **_extra,
) -> int:
    """Write life.html + life_data.json. Returns the number of days on the axis."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    life = compute_life(rows or [], home_city, home_history, trips=trips, music_by_year=music_by_year)
    # the stripes only know countries that were some day's majority country (a transit
    # stop never is), so the headline count comes from the site-wide figure
    life["countries_total"] = country_total or len(life.get("countries", []))
    body = json.dumps(life, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "life_data.json").write_text(body, encoding="utf-8")
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    (out_dir / "life.html").write_text(PAGE_TEMPLATE.format(site=SITE_URL, ver_json=json.dumps(ver)),
                                       encoding="utf-8")
    log.info("life.html: %d days, %d country runs", life.get("days", 0), len(life.get("country_runs", [])))
    return int(life.get("days", 0))
