# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_compare.py — put two years, two home eras, or a year against an era side by side.

``compare.html?a=2015&b=2019`` (``era1``/``era2``/… for eras). The fact sheets come
from `metrics.year_facts` and are written to ``year_facts.json``, fetched by the
page (cache-busted with a content hash). Self-contained like the country pages.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from make_thumbs import thumb_url
from metrics.year_facts import compute_year_facts
from year_covers import select_year_covers

log = logging.getLogger(__name__)

SITE_URL = "https://4sq.pages.dev"

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Compare years · Foursquare history</title>
<meta name="description" content="Two years or two eras of check-ins, side by side.">
<link rel="canonical" href="{site}/compare.html">
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
.wrap{{max-width:960px;margin:0 auto;padding:88px 22px 120px;}}
.hero-name{{font-family:'Playfair Display',serif;font-size:clamp(2.2rem,6vw,3.8rem);font-weight:900;line-height:1.08;letter-spacing:-.02em;padding-bottom:.08em;background:linear-gradient(150deg,#f5d48a 0%,#e8b86d 40%,#b97c30 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero-sub{{font-family:'DM Mono',monospace;font-size:.66rem;text-transform:uppercase;letter-spacing:.16em;color:var(--muted);margin-top:12px;}}
.pick{{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;margin:30px 0 8px;}}
select{{width:100%;background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font:600 1rem 'DM Sans',sans-serif;}}
#selA{{border-color:var(--gold);}} #selB{{border-color:var(--teal);}}
button{{background:var(--card2);color:var(--muted);border:1px solid var(--border);border-radius:10px;padding:9px 12px;font-family:'DM Mono',monospace;cursor:pointer;}}
button:hover{{color:var(--gold);}}
section{{margin-top:44px;}}
.sec-h{{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.2em;color:var(--gold);margin-bottom:14px;}}
table{{width:100%;border-collapse:collapse;}}
th,td{{padding:9px 8px;border-bottom:1px solid var(--border);text-align:right;font-variant-numeric:tabular-nums;}}
th{{font-family:'DM Mono',monospace;font-size:.56rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:400;}}
td:first-child,th:first-child{{text-align:left;color:var(--muted);font-size:.85rem;}}
td.a{{color:var(--gold);font-weight:600;}} td.b{{color:var(--teal);font-weight:600;}}
td.d{{font-family:'DM Mono',monospace;font-size:.72rem;}}
.up{{color:var(--teal);}} .down{{color:var(--rose);}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:26px;}}
.rank-row{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:4px 0;font-size:.85rem;}}
.rank-row span:first-child{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.rank-row span:last-child{{font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);}}
.col-h{{font-family:'DM Mono',monospace;font-size:.56rem;letter-spacing:.12em;text-transform:uppercase;margin-bottom:6px;}}
.bars{{display:flex;align-items:flex-end;gap:3px;height:120px;}}
.bar-col{{flex:1 1 0;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0;}}
.pair{{display:flex;align-items:flex-end;gap:1px;height:100px;width:100%;justify-content:center;}}
.pair i{{display:block;width:45%;border-radius:2px 2px 0 0;min-height:1px;}}
.pair i.a{{background:var(--gold);}} .pair i.b{{background:var(--teal);}}
.bar-lbl{{font-family:'DM Mono',monospace;font-size:.48rem;color:var(--muted);}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;}}
.chip{{font-size:.78rem;border:1px solid var(--border);border-radius:999px;padding:2px 10px;}}
.note{{color:var(--muted);font-size:.85rem;margin-top:24px;}}
@media(max-width:600px){{.wrap{{padding:78px 16px 90px;}}.cols{{grid-template-columns:1fr;}}th,td{{padding:8px 4px;}}}}
/* 2026 refresh: face-off header, tug-of-war rows, cards */
:root{{--ca:#f6c27a;--cb:#ff7aa8;}}
.duel{{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:stretch;margin:30px 0 8px;}}
.side{{position:relative;border-radius:22px;overflow:hidden;border:1px solid var(--border);min-height:190px;display:flex;flex-direction:column;justify-content:flex-end;padding:18px;background:var(--card);}}
.side .bg{{position:absolute;inset:0;background:center/cover no-repeat;filter:brightness(.38);transition:opacity .4s;}}
.side::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 30%,rgba(10,12,18,.85));pointer-events:none;}}
.side>*:not(.bg){{position:relative;z-index:1;}}
.side.a{{border-color:rgba(246,194,122,.45);}} .side.b{{border-color:rgba(255,122,168,.45);}}
.side .big{{font-family:'Playfair Display',serif;font-weight:900;font-size:clamp(2rem,5vw,3.4rem);line-height:1;letter-spacing:-.02em;margin-bottom:10px;}}
.side.a .big{{color:var(--ca);}} .side.b .big{{color:var(--cb);}}
.side select{{background:rgba(10,12,18,.6);backdrop-filter:blur(6px);border-radius:999px;font-size:.85rem;padding:8px 14px;}}
#selA{{border-color:var(--ca);}} #selB{{border-color:var(--cb);}}
.vs{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;}}
.vs span{{font-family:'Playfair Display',serif;font-style:italic;font-size:1.3rem;color:var(--muted);}}
#swap{{border-radius:999px;}}
.mrows{{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:6px 18px;}}
.mrow{{display:grid;grid-template-columns:96px 1fr 96px;gap:14px;align-items:center;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.04);}}
.mrow:last-child{{border-bottom:none;}}
.mv{{font-family:'Playfair Display',serif;font-size:1.25rem;font-weight:700;font-variant-numeric:tabular-nums;}}
.mv.a{{color:var(--ca);}} .mv.b{{color:var(--cb);text-align:right;}}
.mv.lose{{opacity:.5;}}
.mmid{{min-width:0;}}
.mlbl{{display:flex;justify-content:space-between;gap:8px;font-size:.78rem;color:var(--muted);margin-bottom:5px;}}
.mlbl b{{font-family:'DM Mono',monospace;font-size:.62rem;font-weight:400;white-space:nowrap;}}
.split{{display:flex;gap:2px;height:6px;border-radius:999px;overflow:hidden;background:var(--card2);}}
.split i{{display:block;height:100%;}}
.split i.a{{background:var(--ca);}} .split i.b{{background:var(--cb);}}
.up{{color:#7fe0b0;}} .down{{color:#ff8f9e;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:16px 18px;}}
.sec-h{{color:#ff9a6b;}}
.pair i.a{{background:var(--ca);}} .pair i.b{{background:var(--cb);}}
.chip{{background:rgba(255,255,255,.04);}}
.col-h.a{{color:var(--ca);}} .col-h.b{{color:var(--cb);}}
.rank-row.both span:first-child::before{{content:'◆ ';color:#ff9a6b;font-size:.6rem;}}
.legend{{font-family:'DM Mono',monospace;font-size:.56rem;color:var(--muted);letter-spacing:.08em;margin-top:10px;}}
.hero-name{{display:inline-block;background:linear-gradient(150deg,#f6d38b 0%,#ff9a6b 55%,#ff6fae 100%);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;}}
@media(max-width:600px){{.duel{{gap:6px;}}.vs span{{display:none;}}#swap{{padding:8px;}}.side{{min-height:130px;padding:12px;border-radius:16px;}}.side .big{{font-size:1.7rem;}}.side select{{font-size:.75rem;padding:6px 8px;}}.mrow{{grid-template-columns:70px 1fr 70px;gap:8px;}}.mv{{font-size:1rem;}}.mrows,.card{{padding:6px 12px;}}}}
</style>
</head>
<body>
<a class="crumb" href="/stats.html">← stats</a>
<div class="wrap">
<h1 class="hero-name">Compare</h1>
<div class="hero-sub">two years, two home eras, or one against the other</div>
<div class="duel">
<div class="side a"><div class="bg" id="bgA"></div><div class="big" id="bigA">…</div><select id="selA" aria-label="First period"></select></div>
<div class="vs"><span>vs</span><button id="swap" title="Swap" aria-label="Swap">⇄</button></div>
<div class="side b"><div class="bg" id="bgB"></div><div class="big" id="bigB">…</div><select id="selB" aria-label="Second period"></select></div>
</div>
<div id="out"><p class="note">Loading…</p></div>
</div>
<script>
const VER = {ver_json}, COVERS = {covers_json};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
const fmt = n => n == null ? '—' : Number(n).toLocaleString('en-US');
const ROWS = [
  ['checkins', 'Check-ins'], ['days', 'Active days'], ['per_day', 'Check-ins per active day'],
  ['venues', 'Venues'], ['new_venues', 'New venues'], ['cities', 'Cities'], ['new_cities', 'New cities'],
  ['countries', 'Countries'], ['trips', 'Trips started'], ['trip_days', 'Days on trips'],
  ['farthest_km', 'Farthest day (km)'], ['rg_km', 'Radius of gyration (km)'], ['home_pct', 'At home city (%)'],
  ['companions', 'Companions'], ['shouts', 'Shouts'], ['photos', 'Photos'], ['reconstructed', 'Reconstructed']
];
const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
let D = null;
function opts() {{
  const ys = Object.keys(D.years).sort().reverse();
  return '<optgroup label="Home eras">' + D.eras.map(e => '<option value="' + e.id + '">' + esc(e.label) + '</option>').join('') +
    '</optgroup><optgroup label="Years">' + ys.map(y => '<option value="' + y + '">' + y + '</option>').join('') + '</optgroup>';
}}
function get(k) {{
  if (D.years[k]) return {{ label: k, f: D.years[k] }};
  const e = D.eras.find(e => e.id === k);
  return e ? {{ label: e.label, f: e.facts }} : null;
}}
function delta(a, b) {{
  if (a == null || b == null) return '';
  const d = b - a, cls = d > 0 ? 'up' : d < 0 ? 'down' : '';
  const pct = a ? ' (' + (d > 0 ? '+' : '') + Math.round(100 * d / a) + '%)' : '';
  return '<span class="' + cls + '">' + (d > 0 ? '+' : '') + fmt(Math.round(d * 100) / 100) + pct + '</span>';
}}
function ranks(list, other) {{
  const o = new Set((other || []).map(([k]) => k));
  return list.length ? list.map(([k, n]) => '<div class="rank-row' + (o.has(k) ? ' both' : '') + '"><span title="' + esc(k) + '">' + esc(k) + '</span><span>' + fmt(n) + '</span></div>').join('') : '<p class="note">—</p>';
}}
function sideBySide(title, key, A, B) {{
  return '<section><div class="sec-h">' + title + '</div><div class="card"><div class="cols"><div><div class="col-h a">' + esc(A.label) + '</div>' +
    ranks(A.f[key], B.f[key]) + '</div><div><div class="col-h b">' + esc(B.label) + '</div>' + ranks(B.f[key], A.f[key]) + '</div></div></div></section>';
}}
function mrow(l, a, b) {{
  const tot = (+a || 0) + (+b || 0), pa = tot ? 100 * (+a || 0) / tot : 0;
  return '<div class="mrow"><div class="mv a' + (+a < +b ? ' lose' : '') + '">' + fmt(a) + '</div><div class="mmid"><div class="mlbl"><span>' + l + '</span><b>' + delta(a, b) + '</b></div>' +
    '<div class="split"><i class="a" style="width:' + pa.toFixed(1) + '%"></i><i class="b" style="width:' + (tot ? 100 - pa : 0).toFixed(1) + '%"></i></div></div>' +
    '<div class="mv b' + (+b < +a ? ' lose' : '') + '">' + fmt(b) + '</div></div>';
}}
function face(side, key, o) {{
  document.getElementById('big' + side).textContent = o.label;
  const c = COVERS[key], bg = document.getElementById('bg' + side);
  bg.style.backgroundImage = c ? 'url(' + JSON.stringify(c) + ')' : '';
}}
function pairBars(title, a, b, labels) {{
  // shares, not counts, so a 3-month era and a full year read on one scale
  const sa = a.reduce((x, y) => x + y, 0) || 1, sb = b.reduce((x, y) => x + y, 0) || 1;
  const pa = a.map(v => v / sa), pb = b.map(v => v / sb), mx = Math.max(...pa, ...pb) || 1;
  return '<section><div class="sec-h">' + title + ' <span style="color:var(--muted)">· share of check-ins</span></div><div class="card"><div class="bars">' +
    labels.map((l, i) => '<div class="bar-col" title="' + esc(l) + ': ' + Math.round(pa[i] * 100) + '% / ' + Math.round(pb[i] * 100) + '%"><div class="pair">' +
      '<i class="a" style="height:' + Math.round(100 * pa[i] / mx) + 'px"></i><i class="b" style="height:' + Math.round(100 * pb[i] / mx) + 'px"></i></div>' +
      '<span class="bar-lbl">' + esc(l) + '</span></div>').join('') + '</div></div></section>';
}}
function render() {{
  const ka = selA.value, kb = selB.value, A = get(ka), B = get(kb);
  history.replaceState(null, '', '?a=' + encodeURIComponent(ka) + '&b=' + encodeURIComponent(kb));
  if (!A || !B) return;
  document.title = A.label + ' vs ' + B.label + ' · Foursquare history';
  face('A', ka, A); face('B', kb, B);
  let h = '<section><div class="mrows">' +
    ROWS.filter(([k]) => A.f[k] != null || B.f[k] != null).map(([k, l]) => mrow(l, A.f[k], B.f[k])).join('') +
    '</div></section>';
  const nc = (o, s) => '<div><div class="col-h ' + s + '">' + esc(o.label) + '</div><div class="chips">' +
    (o.f.new_countries.length ? o.f.new_countries.map(x => '<span class="chip">' + esc(x) + '</span>').join('') : '<span class="note" style="margin:0">none</span>') + '</div></div>';
  h += '<section><div class="sec-h">First time in</div><div class="card"><div class="cols">' + nc(A, 'a') + nc(B, 'b') + '</div></div></section>';
  h += pairBars('Hour of day', A.f.hours, B.f.hours, A.f.hours.map((_, i) => String(i).padStart(2, '0')));
  h += pairBars('Day of week', A.f.weekdays, B.f.weekdays, WD);
  h += sideBySide('Top categories', 'top_categories', A, B) + sideBySide('Top cities', 'top_cities', A, B) +
    sideBySide('Top countries', 'top_countries', A, B) + sideBySide('Top venues', 'top_venues', A, B) +
    sideBySide('Most often with', 'top_companions', A, B) + '<p class="legend">◆ in both top lists</p>';
  document.getElementById('out').innerHTML = h;
}}
const selA = document.getElementById('selA'), selB = document.getElementById('selB');
fetch('year_facts.json?v=' + VER).then(r => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }}).then(j => {{
  D = j;
  selA.innerHTML = selB.innerHTML = opts();
  const ys = Object.keys(D.years).sort(), q = new URLSearchParams(location.search);
  const ok = k => k && get(k);
  selA.value = ok(q.get('a')) ? q.get('a') : (ys[ys.length - 2] || ys[0]);
  selB.value = ok(q.get('b')) ? q.get('b') : ys[ys.length - 1];
  selA.onchange = selB.onchange = render;
  document.getElementById('swap').onclick = () => {{ [selA.value, selB.value] = [selB.value, selA.value]; render(); }};
  render();
}}).catch(e => {{ document.getElementById('out').innerHTML = '<p class="note">Could not load year_facts.json (' + esc(e.message) + ').</p>'; }});
</script>
</body>
</html>"""


def build_page(
    out_path: str = "",
    rows: list | None = None,
    trips: list | None = None,
    stats_data: dict | None = None,
    photos_by_checkin: dict | None = None,
    home_city: str = "",
    home_history: list | None = None,
    config_dir: str = "",
    pix_url: str = "",
    **_extra,
) -> int:
    """Write compare.html + year_facts.json. Returns the number of buckets."""
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats_data = stats_data or {}
    facts = compute_year_facts(rows or [], home_city, home_history, trips=trips,
                               photos_by_checkin=photos_by_checkin,
                               distance_from_home=stats_data.get("distance_from_home"),
                               coverage=stats_data.get("coverage"))
    body = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "year_facts.json").write_text(body, encoding="utf-8")
    ver = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    # the year's album cover sits behind each side of the face-off (eras go plain)
    covers = {}
    if config_dir and pix_url:
        covers = {str(y): thumb_url(c["src"]) for y, c in
                  select_year_covers(rows or [], photos_by_checkin or {}, config_dir, pix_url=pix_url).items()}
    (out_dir / "compare.html").write_text(
        PAGE_TEMPLATE.format(site=SITE_URL, ver_json=json.dumps(ver),
                             covers_json=json.dumps(covers, ensure_ascii=False).replace("</", "<\\/")),
        encoding="utf-8")
    n = len(facts["years"]) + len(facts["eras"])
    log.info("compare.html: %d years + %d eras", len(facts["years"]), len(facts["eras"]))
    return n
