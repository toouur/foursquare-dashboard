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
:root{{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.55;overflow-x:hidden;}}
a{{color:var(--teal);}}
.crumb{{position:fixed;top:14px;left:18px;z-index:200;font-family:'DM Mono',monospace;font-size:.58rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);background:rgba(18,21,31,.85);backdrop-filter:blur(8px);border:1px solid var(--border);padding:6px 12px;border-radius:6px;text-decoration:none;}}
.crumb:hover{{color:var(--gold);}}
.wrap{{max-width:1280px;margin:0 auto;padding:88px 22px 120px;}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,6vw,4rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.tools{{display:flex;gap:6px;align-items:center;margin:28px 0 10px;flex-wrap:wrap;}}
.tools button{{background:var(--card2);color:var(--muted);border:1px solid var(--border);border-radius:8px;padding:5px 11px;font-family:'DM Mono',monospace;font-size:.7rem;cursor:pointer;}}
.tools button.on,.tools button:hover{{color:var(--gold);border-color:var(--gold);}}
.readout{{font-family:'DM Mono',monospace;font-size:.7rem;color:var(--text);margin-left:auto;min-height:1.2em;text-align:right;}}
.chart{{display:grid;grid-template-columns:92px 1fr;background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;}}
.labels{{border-right:1px solid var(--border);}}
.labels div,.lane{{display:flex;align-items:center;border-bottom:1px solid var(--border);}}
.labels div{{font-family:'DM Mono',monospace;font-size:.54rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);padding:0 10px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}}
.labels div.sub{{text-transform:none;letter-spacing:.02em;font-size:.6rem;color:var(--text);}}
.scroll{{overflow-x:auto;overflow-y:hidden;position:relative;}}
.inner{{position:relative;min-width:100%;}}
.lane{{position:relative;overflow:hidden;}}
.lane svg{{display:block;width:100%;}}
.ticks{{height:22px;position:relative;border-bottom:1px solid var(--border);}}
.ticks span{{position:absolute;top:4px;font-family:'DM Mono',monospace;font-size:.5rem;color:var(--muted);transform:translateX(3px);border-left:1px solid var(--border);padding-left:3px;}}
.ov{{position:absolute;top:0;bottom:0;display:flex;align-items:center;overflow:hidden;white-space:nowrap;font-size:.62rem;padding:0 4px;color:#0a0c12;font-weight:600;}}
.music .ov{{color:var(--text);font-weight:400;border-left:1px solid var(--border);}}
.trip-link rect{{cursor:pointer;}}
.trip-link:hover rect{{fill:#f5a3b1;}}
.cursor{{position:absolute;top:0;bottom:0;width:1px;background:rgba(232,184,109,.7);pointer-events:none;display:none;}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:14px;font-size:.74rem;color:var(--muted);}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px;}}
.note{{color:var(--muted);font-size:.85rem;margin-top:18px;max-width:760px;}}
@media(max-width:600px){{.wrap{{padding:78px 12px 90px;}}.chart{{grid-template-columns:64px 1fr;}}.labels div{{padding:0 6px;font-size:.48rem;}}}}
</style>
</head>
<body>
<a class="crumb" href="/stats.html">← stats</a>
<div class="wrap">
<h1 class="hero-name">Life in Stripes</h1>
<div class="hero-sub" id="sub">life in stripes</div>
<div class="tools"><span style="font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted)">zoom</span>
<button data-z="1" class="on">1×</button><button data-z="2">2×</button><button data-z="4">4×</button><button data-z="10">10×</button>
<div class="readout" id="readout" aria-live="polite"></div></div>
<div class="chart"><div class="labels" id="labels"></div><div class="scroll" id="scroll"><div class="inner" id="inner"></div></div></div>
<div class="legend" id="legend"></div>
<p class="note">Click a trip in the trips row to open it. Country: the day's most-checked-in country; a day without check-ins keeps the previous one and is drawn faded. The most-lived countries get quiet colours so travel stands out. Density is check-ins per week; people rows show check-ins together per month.</p>
</div>
<script>
const VER = {ver_json};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const NS = 'http://www.w3.org/2000/svg';
let L = null, DAYS = 0, T0 = 0, COLORS = [];
const dayStr = i => new Date(T0 + i * 864e5).toISOString().slice(0, 10);
function svg(h, parts) {{ return '<svg xmlns="' + NS + '" viewBox="0 0 ' + DAYS + ' ' + h + '" preserveAspectRatio="none" style="height:' + h + 'px">' + parts.join('') + '</svg>'; }}
function rect(x, w, y, h, fill, extra) {{ return '<rect x="' + x + '" width="' + w + '" y="' + y + '" height="' + h + '" fill="' + fill + '"' + (extra || '') + '/>'; }}
function pct(i) {{ return (100 * i / DAYS) + '%'; }}
function find(runs, i) {{   // runs sorted by start: [start, len, …]
  let lo = 0, hi = runs.length - 1;
  while (lo <= hi) {{ const m = (lo + hi) >> 1, r = runs[m]; if (i < r[0]) hi = m - 1; else if (i >= r[0] + r[1]) lo = m + 1; else return r; }}
  return null;
}}
function build() {{
  T0 = Date.parse(L.start + 'T00:00:00Z'); DAYS = L.days;
  const cdays = L.countries.map(() => 0);
  L.country_runs.forEach(r => cdays[r[2]] += r[1]);
  const order = cdays.map((d, i) => [d, i]).sort((a, b) => b[0] - a[0]).map(p => p[1]);
  const quiet = ['#3a4058', '#4a4560'];
  order.forEach((ci, k) => {{ COLORS[ci] = k < 2 ? quiet[k] : 'hsl(' + Math.round((k * 137.508) % 360) + ',62%,62%)'; }});
  const lab = [], lanes = [];
  const add = (label, h, html, cls) => {{ lab.push('<div class="' + (cls || '') + '" style="height:' + h + 'px" title="' + esc(label) + '">' + esc(label) + '</div>'); lanes.push('<div class="lane ' + (cls || '') + '" style="height:' + h + 'px">' + html + '</div>'); }};
  // year ticks
  let ticks = '';
  const y0 = +L.start.slice(0, 4), y1 = +dayStr(DAYS - 1).slice(0, 4);
  for (let y = y0 + 1; y <= y1; y++) {{ const i = (Date.parse(y + '-01-01T00:00:00Z') - T0) / 864e5; ticks += '<span style="left:' + pct(i) + '">' + y + '</span>'; }}
  lab.push('<div style="height:22px"></div>');
  lanes.push('<div class="ticks">' + ticks + '</div>');
  const eraFill = {{}}; let ek = 0;
  add('home', 30, svg(30, L.eras.map(e => {{ if (!(e[2] in eraFill)) eraFill[e[2]] = ['#e8b86d', '#4ec9b0', '#e8778a'][ek++ % 3]; return rect(e[0], e[1], 0, 30, eraFill[e[2]]); }})) +
    L.eras.map(e => '<div class="ov" style="left:' + pct(e[0]) + ';width:' + pct(e[1]) + '">' + esc(e[2]) + '</div>').join(''));
  add('country', 34, svg(34, L.country_runs.map(r => rect(r[0], r[1], 0, 34, COLORS[r[2]], r[3] ? ' fill-opacity=".45"' : ''))));
  // each trip is a real link with its own hit area; a click anywhere else does nothing
  add('trips', 22, svg(22, L.trips.map(t => {{
    const r = '<rect x="' + t[0] + '" width="' + t[1] + '" y="3" height="16" fill="#e8778a"><title>' + esc(t[2]) + '</title></rect>';
    return t[3] != null ? '<a href="/trips.html#trip-' + t[3] + '" class="trip-link">' + r + '</a>' : r;
  }})));
  const mx = Math.sqrt(Math.max(1, ...L.weeks));
  add('density', 40, svg(40, L.weeks.map((n, k) => n ? rect(k * 7, 7, 40 - 40 * Math.sqrt(n) / mx, 40 * Math.sqrt(n) / mx, '#e8b86d') : '')));
  if (L.people.length) {{
    const mStart = L.months.map(m => (Date.parse(m + '-01T00:00:00Z') - T0) / 864e5);
    L.people.forEach(([name, cnt]) => {{
      const top = Math.max(1, ...cnt);
      add(name, 16, svg(16, cnt.map((n, k) => n ? rect(Math.max(0, mStart[k]), (k + 1 < mStart.length ? mStart[k + 1] : DAYS) - Math.max(0, mStart[k]), 2, 12, '#4ec9b0', ' fill-opacity="' + (0.15 + 0.85 * n / top).toFixed(2) + '"') : '')), 'sub');
    }});
  }}
  if (L.music.length) {{
    add('music', 26, L.music.map(([y, artist, plays]) => {{
      const a = Math.max(0, (Date.parse(y + '-01-01T00:00:00Z') - T0) / 864e5), b = Math.min(DAYS, (Date.parse((+y + 1) + '-01-01T00:00:00Z') - T0) / 864e5);
      return '<div class="ov" style="left:' + pct(a) + ';width:' + pct(b - a) + '" title="' + esc(y + ': ' + artist + ' · ' + plays + ' plays') + '">' + esc(artist) + '</div>';
    }}).join(''), 'music');
  }}
  document.getElementById('labels').innerHTML = lab.join('');
  document.getElementById('inner').innerHTML = lanes.join('') + '<div class="cursor" id="cursor"></div>';
  document.getElementById('legend').innerHTML = order.slice(0, 16).map(ci => '<span><i style="background:' + COLORS[ci] + '"></i>' + esc(L.countries[ci]) + ' · ' + cdays[ci].toLocaleString('en-US') + ' d</span>').join('');
  document.getElementById('sub').textContent = L.start + ' → ' + dayStr(DAYS - 1) + ' · ' + DAYS.toLocaleString('en-US') + ' days · ' + L.countries.length + ' countries · ' + L.trips.length + ' trips';
}}
function readout(i) {{
  const r = find(L.country_runs, i), e = find(L.eras, i), t = L.trips.filter(t => i >= t[0] && i < t[0] + t[1]);
  const parts = [dayStr(i)];
  if (e) parts.push('home ' + e[2]);
  if (r) parts.push(L.countries[r[2]] + (r[3] ? ' (no check-in)' : ''));
  if (t.length) parts.push('✈ ' + t.map(x => x[2]).join(', '));
  parts.push(L.weeks[Math.floor(i / 7)] + ' check-ins that week');
  return parts.join(' · ');
}}
const inner = document.getElementById('inner'), out = document.getElementById('readout');
inner.addEventListener('mousemove', ev => {{
  const box = inner.getBoundingClientRect(), i = Math.max(0, Math.min(DAYS - 1, Math.floor((ev.clientX - box.left) / box.width * DAYS)));
  const c = document.getElementById('cursor'); c.style.display = 'block'; c.style.left = pct(i);
  out.textContent = readout(i);
}});
inner.addEventListener('mouseleave', () => {{ const c = document.getElementById('cursor'); if (c) c.style.display = 'none'; }});
document.querySelectorAll('.tools button').forEach(b => b.onclick = () => {{
  const sc = document.getElementById('scroll'), mid = (sc.scrollLeft + sc.clientWidth / 2) / inner.offsetWidth;
  document.querySelectorAll('.tools button').forEach(x => x.classList.toggle('on', x === b));
  inner.style.width = (100 * +b.dataset.z) + '%';
  sc.scrollLeft = mid * inner.offsetWidth - sc.clientWidth / 2;
}});
fetch('life_data.json?v=' + VER).then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
  .then(j => {{ L = j; if (!L.days) throw new Error('no data'); build(); document.getElementById('scroll').scrollLeft = 1e9; }})
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
    **_extra,
) -> int:
    """Write life.html + life_data.json. Returns the number of days on the axis."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    life = compute_life(rows or [], home_city, home_history, trips=trips, music_by_year=music_by_year)
    body = json.dumps(life, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "life_data.json").write_text(body, encoding="utf-8")
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    (out_dir / "life.html").write_text(PAGE_TEMPLATE.format(site=SITE_URL, ver_json=json.dumps(ver)),
                                       encoding="utf-8")
    log.info("life.html: %d days, %d country runs", life.get("days", 0), len(life.get("country_runs", [])))
    return int(life.get("days", 0))
