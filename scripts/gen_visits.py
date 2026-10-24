# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_visits.py — visits.html, the owner's private view of /api/visits.

A static shell with no data in it: it asks for the STATS_KEY once (kept in
localStorage), fetches /api/visits, and draws totals, a daily chart and the
top paths / countries / cities / referrers / networks plus the latest views
with IPs. Opening it also marks this browser as the owner's (localStorage
`fsq_owner`), so the hit snippet flags these views `owner = 1` and the stats
leave them out. noindex, not in the nav or the sitemap.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Visits · private</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:wght@400;500;600&family=DM+Mono&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0c12;--card:#12141c;--card2:#181a24;--border:#222738;--text:#e0e2ec;--muted:#7a85a8;--gold:#e8b86d;--teal:#4ec9b0;--rose:#e8778a;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;line-height:1.5;}
a{color:var(--teal);text-decoration:none;}
.wrap{max-width:1200px;margin:0 auto;padding:40px 22px 100px;}
h1{font-family:'Playfair Display',serif;font-size:2.6rem;font-weight:900;color:var(--gold);}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:18px 0;}
.bar button,.bar input{background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px 12px;font-family:'DM Mono',monospace;font-size:.72rem;}
.bar button{cursor:pointer;color:var(--muted);} .bar button.on,.bar button:hover{color:var(--gold);border-color:var(--gold);}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:10px 0 24px;}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;text-align:center;}
.kpi b{display:block;font-family:'Playfair Display',serif;font-size:1.9rem;color:var(--gold);}
.kpi span{font-family:'DM Mono',monospace;font-size:.56rem;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);}
.chart{display:flex;align-items:flex-end;gap:2px;height:140px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:24px;}
.chart i{flex:1;background:linear-gradient(180deg,#f5d48a,#b97c30);border-radius:2px 2px 0 0;min-height:1px;}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px;}
.card h2{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:10px;}
table{width:100%;border-collapse:collapse;font-size:.8rem;}
td{padding:4px 6px;border-bottom:1px solid var(--border);vertical-align:top;overflow-wrap:anywhere;}
td.n{text-align:right;font-family:'DM Mono',monospace;color:var(--teal);white-space:nowrap;}
.full{grid-column:1/-1;} .muted{color:var(--muted);font-size:.72rem;}
.note{color:var(--muted);font-size:.85rem;margin-top:14px;}
</style>
</head>
<body>
<div class="wrap">
<h1>Visits</h1>
<div class="muted">private · views of this site from /api/hit · the owner's own browser is excluded</div>
<div class="bar" id="keybar" hidden><input id="key" type="password" placeholder="STATS_KEY" autocomplete="off"><button id="save">Unlock</button></div>
<div class="bar" id="ctl"><button data-d="1">24 h</button><button data-d="7">7 days</button><button data-d="30" class="on">30 days</button><button data-d="90">90 days</button><button id="forget">Forget key</button></div>
<div id="out"><p class="note">Loading…</p></div>
</div>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { return null; } };
ls('fsq_owner', '1');                       // this browser is the owner's: its own views stop counting
let days = 30;
const out = document.getElementById('out');
function table(title, rows, cols, cls) {
  return '<div class="card ' + (cls || '') + '"><h2>' + title + '</h2><table>' + (rows.length ? rows.map(r => '<tr>' + cols.map(([k, n, f]) => '<td' + (n ? ' class="n"' : '') + '>' + (f ? f(r) : esc(r[k] ?? '—')) + '</td>').join('') + '</tr>').join('') : '<tr><td class="muted">nothing yet</td></tr>') + '</table></div>';
}
const host = u => { try { return new URL(u).host; } catch (e) { return u; } };
const when = ts => new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16);
const agent = ua => { ua = ua || ''; const b = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '?';
  const o = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /Mac OS/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '?'; return b + ' · ' + o; };
async function load() {
  const key = ls('fsq_stats_key');
  if (!key) { document.getElementById('keybar').hidden = false; out.innerHTML = '<p class="note">Enter the STATS_KEY set in Cloudflare Pages → Settings → Variables and secrets.</p>'; return; }
  out.innerHTML = '<p class="note">Loading…</p>';
  let r;
  try { r = await fetch('/api/visits?days=' + days, { headers: { 'X-Stats-Key': key } }); } catch (e) { out.innerHTML = '<p class="note">Network error.</p>'; return; }
  if (r.status === 401) { ls('fsq_stats_key', null); document.getElementById('keybar').hidden = false; out.innerHTML = '<p class="note">Wrong key.</p>'; return; }
  const D = await r.json().catch(() => ({ error: 'bad response' }));
  if (D.error) { out.innerHTML = '<p class="note">' + esc(D.error) + '</p>'; return; }
  const T = D.totals || {}, mx = Math.max(1, ...D.daily.map(x => x.views));
  out.innerHTML =
    '<div class="kpis">' + [[T.views, 'views'], [T.visitors, 'unique visitors'], [T.ips, 'unique IPs'], [D.countries.length, 'countries'], [D.cities.length, 'cities']].map(([a, b]) => '<div class="kpi"><b>' + (a ?? 0).toLocaleString('en-US') + '</b><span>' + b + '</span></div>').join('') + '</div>' +
    '<div class="chart" title="views per day (UTC)">' + D.daily.map(x => '<i style="height:' + Math.round(100 * x.views / mx) + '%" title="' + esc(x.d) + ': ' + x.views + ' views, ' + x.visitors + ' visitors"></i>').join('') + '</div>' +
    '<div class="grid">' +
    table('Pages', D.paths, [['path', 0, r => '<a href="' + esc(r.path) + '">' + esc(r.path) + '</a>'], ['n', 1], ['u', 1, r => r.u + ' u']]) +
    table('Countries', D.countries, [['country'], ['n', 1], ['u', 1, r => r.u + ' u']]) +
    table('Cities', D.cities, [['city', 0, r => esc(r.city || '—') + ' <span class="muted">' + esc(r.country || '') + '</span>'], ['n', 1], ['u', 1, r => r.u + ' u']]) +
    table('Referrers', D.refs, [['ref', 0, r => '<span title="' + esc(r.ref) + '">' + esc(host(r.ref)) + '</span>'], ['n', 1]]) +
    table('Networks', D.orgs, [['org', 0, r => esc(r.org || '—') + ' <span class="muted">AS' + esc(r.asn || '?') + '</span>'], ['n', 1], ['ips', 1, r => r.ips + ' IPs']]) +
    table('Latest views', D.recent, [['ts', 0, r => '<span class="muted">' + when(r.ts) + '</span>'], ['path', 0, r => esc(r.path)],
      ['ip', 0, r => esc(r.ip) + '<br><span class="muted">' + esc([r.city, r.region, r.country].filter(Boolean).join(', ')) + ' · ' + esc(r.org || '') + '</span>'],
      ['ua', 0, r => '<span class="muted">' + esc(agent(r.ua)) + (r.ref ? ' · from ' + esc(host(r.ref)) : '') + ' · ' + esc((r.vid || '').slice(0, 6)) + '</span>']], 'full') +
    '</div><p class="note">Kept 90 days. Times UTC. “u” = distinct visitors (browser id, or the IP when the browser keeps no storage).</p>';
}
document.getElementById('save').onclick = () => { const v = document.getElementById('key').value.trim(); if (v) { ls('fsq_stats_key', v); document.getElementById('keybar').hidden = true; load(); } };
document.getElementById('key').onkeydown = e => { if (e.key === 'Enter') document.getElementById('save').click(); };
document.getElementById('forget').onclick = () => { ls('fsq_stats_key', null); load(); };
document.querySelectorAll('#ctl button[data-d]').forEach(b => b.onclick = () => { days = +b.dataset.d; document.querySelectorAll('#ctl button[data-d]').forEach(x => x.classList.toggle('on', x === b)); load(); });
load();
</script>
</body>
</html>"""


def build_page(out_path: str = "", **_extra) -> None:
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "visits.html").write_text(PAGE, encoding="utf-8")
    log.info("visits.html written (private, STATS_KEY-gated)")
