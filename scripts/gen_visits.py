# Copyright 2026 Andrei Patsiomkin
# SPDX-License-Identifier: Apache-2.0

"""
gen_visits.py — visits.html, the owner's private view of /api/visits.

A static shell with no data in it: it asks for the STATS_KEY once (kept in
localStorage), fetches /api/visits, and draws traffic totals, browser/device profile, approximate
postal areas, engagement/session metrics, and the latest views with IP and location.
Opening it also marks this browser as the owner's (localStorage
`fsq_owner`), so the hit snippet flags these views `owner = 1` and the stats
leave them out; a toggle switches it back to "visitor" (to test the counter),
and `?fsq_owner=1|0` on any page does the same without the key. It also lists
sessions (page sequence and duration) and where they came from (utm tags and
off-site referrers). "Clear all visits" deletes every recorded view (after a typed
confirmation) to start monitoring from zero. Android model codes are shown with
their marketing name (device-names.json, written next to the page from
config/device_names.json — refresh it with update_device_names.py); an iPhone,
which never reports its model, gets a family guessed from its screen. Network
flags (VPN / relay / timezone mismatch) come from /api/visits, and so do: who is
on the site now, new vs returning visitors per day, top venues with names, site
searches, outbound clicks, scroll depth and Web Vitals (p75), and browser ids
merged into one person when they share a fingerprint and an IP.
noindex, not in the nav or the sitemap.
"""
from __future__ import annotations

import json
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
[hidden]{display:none!important;}
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
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px;min-width:0;overflow-x:auto;}
.card h2{font-family:'DM Mono',monospace;font-size:.6rem;text-transform:uppercase;letter-spacing:.18em;color:var(--gold);margin-bottom:10px;}
table{width:100%;border-collapse:collapse;font-size:.8rem;}
td{padding:4px 6px;border-bottom:1px solid var(--border);vertical-align:top;overflow-wrap:anywhere;}
td.n{text-align:right;font-family:'DM Mono',monospace;color:var(--teal);white-space:nowrap;}
.full{grid-column:1/-1;} .muted{color:var(--muted);font-size:.72rem;}
.note{color:var(--muted);font-size:.85rem;margin-top:14px;}
.local{display:inline-block;font-family:'DM Mono',monospace;font-size:.6rem;color:#1a1206;background:linear-gradient(135deg,#f6d38b,#ff9a6b);border-radius:999px;padding:1px 7px;}
.engagement-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;}
.engagement-stat{background:var(--card2);border:1px solid var(--border);border-radius:9px;padding:10px 12px;min-width:0;}
.engagement-stat b{display:block;font-family:'Playfair Display',serif;font-size:1.25rem;color:var(--gold);overflow-wrap:anywhere;}
.engagement-stat span{display:block;font-size:.68rem;color:var(--muted);}
.seq{display:flex;flex-wrap:wrap;gap:3px 4px;align-items:center;font-size:.74rem;}
.seq a{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:0 6px;}
.seq i{font-style:normal;color:var(--muted);font-size:.62rem;}
.tag{display:inline-block;font-family:'DM Mono',monospace;font-size:.6rem;border-radius:999px;padding:1px 7px;border:1px solid var(--border);color:var(--muted);}
.tag.utm{color:#1a1206;background:var(--teal);border-color:var(--teal);} .tag.referrer{color:var(--gold);border-color:var(--gold);}
.tag.owner{color:var(--rose);border-color:var(--rose);}
.tag.vpn{color:#1a0a0e;background:var(--rose);border-color:var(--rose);} .tag.relay{color:var(--rose);border-color:var(--rose);}
.tag.tz{color:var(--gold);border-color:var(--gold);} .tag.clean{color:var(--teal);border-color:var(--teal);}
.vg{color:var(--teal);} .vn{color:var(--gold);} .vp{color:var(--rose);}
.chart.nr i{display:flex;flex-direction:column-reverse;background:none;} .chart.nr b{display:block;background:linear-gradient(180deg,#f5d48a,#b97c30);} .chart.nr u{display:block;background:var(--teal);text-decoration:none;}
.now{background:var(--card);border:1px solid var(--teal);border-radius:12px;padding:10px 14px;margin:0 0 18px;font-size:.8rem;}
.now b{color:var(--teal);font-family:'DM Mono',monospace;font-size:.62rem;text-transform:uppercase;letter-spacing:.14em;}
.bar button.danger{color:var(--rose);border-color:var(--rose);} .bar button.danger:hover{background:var(--rose);color:#1a0a0e;}
</style>
</head>
<body>
<div class="wrap">
<h1>Visits</h1>
<div class="muted">private · views of this site from /api/hit · <span id="owner-note">the owner's own browsers are excluded</span></div>
<div class="bar" id="keybar" hidden><input id="key" type="password" placeholder="STATS_KEY" autocomplete="off"><button id="save">Unlock</button></div>
<div class="bar" id="ctl"><button data-d="1">24 h</button><button data-d="7">7 days</button><button data-d="30" class="on">30 days</button><button data-d="90">90 days</button><button id="export" disabled>Download JSON</button><label style="font-size:.72rem;color:var(--muted);display:flex;align-items:center;gap:5px"><input id="mask-ips" type="checkbox" checked style="accent-color:var(--teal)"> Mask IPs in export</label><button id="forget">Forget key</button></div>
<div class="bar" id="own"><button id="owner-toggle" title="Owner browsers are flagged owner = 1 and left out of the stats. Any page also accepts ?fsq_owner=1 or ?fsq_owner=0."></button><label style="font-size:.72rem;color:var(--muted);display:flex;align-items:center;gap:5px"><input id="with-owner" type="checkbox" style="accent-color:var(--teal)"> Include owner views</label><button id="clear" class="danger" title="Delete every recorded view (all periods, owner views too) and start from zero">Clear all visits…</button></div>
<div id="out"><p class="note">Loading…</p></div>
</div>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { return null; } };
// Opening this page marks the browser as the owner's (its views stop counting) unless the
// toggle below has explicitly set it back to '0' to test the counter as a visitor.
if (ls('fsq_owner') === null) ls('fsq_owner', '1');
const ownerToggle = document.getElementById('owner-toggle');
function paintOwner() {
  const mine = ls('fsq_owner') === '1';
  ownerToggle.textContent = mine ? 'This browser: owner · not counted' : 'This browser: visitor · counted';
  ownerToggle.classList.toggle('on', mine);
}
ownerToggle.onclick = () => { ls('fsq_owner', ls('fsq_owner') === '1' ? '0' : '1'); paintOwner(); };
paintOwner();
let days = 30;
let exportData = null;
const exportButton = document.getElementById('export');
const out = document.getElementById('out');
function table(title, rows, cols, cls) {
  return '<div class="card ' + (cls || '') + '"><h2>' + title + '</h2><table>' + (rows.length ? rows.map(r => '<tr>' + cols.map(([k, n, f]) => '<td' + (n ? ' class="n"' : '') + '>' + (f ? f(r) : esc(r[k] ?? '—')) + '</td>').join('') + '</tr>').join('') : '<tr><td class="muted">nothing yet</td></tr>') + '</table></div>';
}
const host = u => { try { return new URL(u).host; } catch (e) { return u; } };
// a referrer on the visitor's own machine or LAN: someone clicked through from a local copy of the site
function isLocalReferrer(raw) {
  if (typeof raw !== 'string' || !raw) return false;
  if (/^file:/i.test(raw)) return true;
  let u;
  try { u = new URL(raw); } catch (e) { return false; }
  if (!['http:', 'https:'].includes(u.protocol)) return false;
  let h = u.hostname.toLowerCase();
  if (h.endsWith('.')) h = h.slice(0, -1);
  if (h.startsWith('[') && h.endsWith(']')) h = h.slice(1, -1);
  if (h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local')) return true;
  if (h.includes(':')) {
    // IPv6 loopback, unspecified, unique-local (fc00::/7), and link-local (fe80::/10).
    return h === '::1' || h === '::' || /^f[cd]/.test(h) || /^fe[89ab]/.test(h);
  }
  const parts = h.split('.');
  if (parts.length !== 4 || parts.some(p => !/^\d{1,3}$/.test(p) || Number(p) > 255)) return false;
  const [a, b] = parts.map(Number);
  return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}
const refHtml = u => {
  if (!isLocalReferrer(u)) return esc(host(u));
  const h = host(u) || 'local file';
  return '<span class="local" title="opened from a local copy: ' + esc(u) + '">⌂ local copy</span> ' + esc(h);
};
// Android model code → marketing name (Google Play's supported-devices list), loaded once.
let deviceNames = {};
const namesReady = fetch('/device-names.json').then(r => r.ok ? r.json() : {}).then(m => { deviceNames = m || {}; }).catch(() => {});
const modelName = code => code ? (deviceNames[code] ? deviceNames[code] + ' (' + code + ')' : code) : '';
// iPhones never report a model; the CSS screen size narrows it to a family.
const IPHONE_SCREENS = {
  '375x667': 'iPhone SE / 6–8', '414x736': 'iPhone 6–8 Plus', '375x812': 'iPhone X / XS / 11 Pro / 12–13 mini',
  '414x896': 'iPhone XR / XS Max / 11 / 11 Pro Max', '390x844': 'iPhone 12–14 / 12–13 Pro / 16e',
  '428x926': 'iPhone 12–13 Pro Max / 14 Plus', '393x852': 'iPhone 14 Pro / 15 / 15 Pro / 16',
  '430x932': 'iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus', '402x874': 'iPhone 16 Pro / 17 / 17 Pro',
  '440x956': 'iPhone 16 Pro Max / 17 Pro Max', '420x912': 'iPhone Air',
};
const iphoneGuess = (ua, scr) => {
  if (!/iPhone/.test(ua || '') || !scr) return '';
  const g = IPHONE_SCREENS[String(scr).split('@')[0]];
  return g ? '≈ ' + g + ' (by screen)' : '';
};
const deviceLabel = r => modelName(r.model) || iphoneGuess(r.ua, r.scr);
const NET_TITLES = {
  vpn: 'VPN / hosting network: the IP location is not where the person is',
  relay: 'privacy relay (iCloud Private Relay, WARP): city hidden, country usually kept',
  tz: 'device timezone has another UTC offset than the IP location: VPN, or a traveller / phone set to another country',
  clean: 'ordinary network, device timezone agrees',
  'no-tz': 'ordinary network; no device timezone sent (older counter), so the timezone was not checked',
};
const NET_LABELS = { vpn: 'VPN', relay: 'relay', tz: 'TZ≠IP', clean: 'clean', 'no-tz': 'no TZ' };
const netTags = flags => (flags || []).map(f => '<span class="tag ' + esc(f) + '" title="' + esc(NET_TITLES[f] || f) + '">' + esc(NET_LABELS[f] || f) + '</span>').join(' ');
const when = ts => new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16);
const fmtDuration = value => {
  const n = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(n) || n < 0) return '—';
  const sec = Math.round(n);
  if (sec < 60) return sec + 's';
  const min = Math.floor(sec / 60), rem = sec % 60;
  if (min < 60) return min + 'm ' + rem + 's';
  return Math.floor(min / 60) + 'h ' + (min % 60) + 'm';
};
const fmtNumber = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
  ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
const agent = ua => { ua = ua || ''; const b = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '?';
  const o = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /Mac OS/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '?'; return b + ' · ' + o; };
async function load() {
  exportData = null;
  exportButton.disabled = true;
  const key = ls('fsq_stats_key');
  if (!key) { document.getElementById('keybar').hidden = false; out.innerHTML = '<p class="note">Enter the STATS_KEY set in Cloudflare Pages → Settings → Variables and secrets.</p>'; return; }
  out.innerHTML = '<p class="note">Loading…</p>';
  let r;
  const withOwner = document.getElementById('with-owner').checked;
  document.getElementById('owner-note').textContent = withOwner ? 'owner views included (marked owner)' : "the owner's own browsers are excluded";
  try { r = await fetch('/api/visits?days=' + days + (withOwner ? '&owner=1' : ''), { headers: { 'X-Stats-Key': encodeURIComponent(key) } }); }
  catch (e) { out.innerHTML = '<p class="note">Request failed: ' + esc(e.message || e) + '. A content blocker may be hiding /api/visits — allow 4sq.pages.dev in it.</p>'; return; }
  if (r.status === 401) { ls('fsq_stats_key', null); document.getElementById('keybar').hidden = false; out.innerHTML = '<p class="note">Wrong key.</p>'; return; }
  const D = await r.json().catch(() => ({ error: 'bad response' }));
  if (D.error) { out.innerHTML = '<p class="note">' + esc(D.error) + '</p>'; return; }
  await namesReady;
  // the export carries the readable names too
  for (const k of ['recent', 'sessionList', 'fingerprints', 'models']) for (const row of (Array.isArray(D[k]) ? D[k] : [])) {
    const name = deviceLabel(row); if (name && name !== row.model) row.model_name = name;
  }
  exportData = D;
  exportButton.disabled = false;
  const arr = name => Array.isArray(D[name]) ? D[name] : [];
  const T = D.totals || {}, engagement = D.engagement || {}, sessions = D.sessions || {};
  const daily = arr('daily'), countries = arr('countries'), cities = arr('cities');
  const mx = Math.max(1, ...daily.map(x => Number(x.views) || 0));
  const nowList = arr('nowOnSite'), nr = arr('newReturning');
  const nrMax = Math.max(1, ...nr.map(x => (Number(x.new) || 0) + (Number(x.returning) || 0)));
  // Web Vitals thresholds (good / needs improvement), web.dev: LCP 2.5/4 s, INP 200/500 ms, CLS 0.1/0.25, TTFB 0.8/1.8 s
  const VT = { lcp: [2500, 4000], inp: [200, 500], cls: [100, 250], ttfb: [800, 1800] };
  const vital = (k, v, n) => {
    if (v === null || v === undefined) return '<span class="muted">—</span>';
    const cls = v <= VT[k][0] ? 'vg' : v <= VT[k][1] ? 'vn' : 'vp';
    const shown = k === 'cls' ? (v / 1000).toFixed(2) : (v >= 1000 ? (v / 1000).toFixed(1) + ' s' : v + ' ms');
    return '<span class="' + cls + '">' + esc(shown) + '</span> <span class="muted">' + esc(n) + '</span>';
  };
  const kpis = [
    [T.views, 'page views'], [T.visitors, 'unique visitors'],
    [D.people == null ? '—' : D.people, 'people (ids merged)'], [T.ips, 'unique IPs'],
    [countries.length, 'countries'], [fmtDuration(engagement.avg_seconds), 'avg visible time'],
    [sessions.avg_pages == null ? '—' : Number(sessions.avg_pages).toFixed(1), 'pages / session']
  ];
  out.innerHTML =
    '<div class="kpis">' + kpis.map(([a, b]) => '<div class="kpi"><b>' + esc(typeof a === 'number' ? a.toLocaleString('en-US') : (a ?? '—')) + '</b><span>' + esc(b) + '</span></div>').join('') + '</div>' +
    '<div class="now"><b>Now on site · last 5 min</b> ' + (nowList.length ? nowList.map(n =>
      '<br><code>' + esc(String(n.who || '').slice(0, 6)) + '</code> ' + (n.owner ? '<span class="tag owner">owner</span> ' : '') +
      '<a href="' + esc(n.path) + '">' + esc(n.path) + '</a> <span class="muted">' + esc([when(n.last).slice(11), n.views + ' views', [n.city, n.country].filter(Boolean).join(', '), n.dev, deviceLabel(n)].filter(Boolean).join(' · ')) + '</span>').join('')
      : '<span class="muted">nobody right now</span>') + '</div>' +
    '<div class="chart" title="views per day (UTC)">' + daily.map(x => '<i style="height:' + Math.round(100 * (Number(x.views) || 0) / mx) + '%" title="' + esc(x.d) + ': ' + esc(x.views) + ' views, ' + esc(x.visitors) + ' visitors"></i>').join('') + '</div>' +
    (nr.length ? '<div class="muted" style="margin:-14px 0 4px">visitors per day · <span class="vn">new</span> / <span class="vg">returning</span></div>' +
      '<div class="chart nr" style="height:90px">' + nr.map(x => { const a = Number(x.new) || 0, b = Number(x.returning) || 0;
        return '<i style="height:' + Math.round(100 * (a + b) / nrMax) + '%" title="' + esc(x.d) + ': ' + a + ' new, ' + b + ' returning"><b style="flex:' + a + '"></b><u style="flex:' + b + '"></u></i>'; }).join('') + '</div>' : '') +
    '<div class="grid">' +
    table('Top venues', arr('topVenues'), [['id', 0, r => '<a href="/venue#' + esc(r.id) + '">' + esc(r.name || r.id) + '</a><br><span class="muted">' + esc([r.category, r.city, r.country].filter(Boolean).join(' · ')) + '</span>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Site searches', arr('searches'), [['data', 0, r => { const s = String(r.data || ''), i = s.indexOf(':');
      return esc(i >= 0 ? s.slice(i + 1) : s) + ' <span class="muted">' + esc(i >= 0 ? s.slice(0, i) : '') + ' · ' + when(r.last) + '</span>'; }], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Outbound clicks', arr('outbound'), [['data', 0, r => '<a href="' + esc(r.data) + '" rel="noopener noreferrer" target="_blank">' + esc(host(r.data)) + '</a> <span class="muted">' + esc(r.data) + '</span>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Scroll depth · how far down', arr('scrollDepth'), [['path', 0, r => esc(r.path)], ['avg_scroll', 1, r => fmtNumber(r.avg_scroll) + '% avg'],
      ['reached_end', 1, r => (Number(r.measured) ? Math.round(100 * Number(r.reached_end) / Number(r.measured)) : 0) + '% to end'], ['measured', 1, r => fmtNumber(r.measured) + ' views']]) +
    table('Web Vitals · p75 per page (green good, gold needs work, red poor; small = samples)', arr('vitals'), [['path', 0, r => esc(r.path) + ' <span class="muted">' + fmtNumber(r.views) + ' views</span>'],
      ['lcp', 1, r => 'LCP ' + vital('lcp', r.lcp, r.lcp_n)], ['inp', 1, r => 'INP ' + vital('inp', r.inp, r.inp_n)],
      ['cls', 1, r => 'CLS ' + vital('cls', r.cls, r.cls_n)], ['ttfb', 1, r => 'TTFB ' + vital('ttfb', r.ttfb, r.ttfb_n)]], 'full') +
    table('Pages', arr('paths'), [['path', 0, r => '<a href="' + esc(r.path) + '">' + esc(r.path) + '</a>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Countries', countries, [['country'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Cities', cities, [['city', 0, r => esc(r.city || '—') + ' <span class="muted">' + esc(r.country || '') + '</span>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Referrers', arr('refs'), [['ref', 0, r => '<span title="' + esc(r.ref) + '">' + refHtml(r.ref) + '</span>'], ['n', 1]]) +
    table('Networks', arr('orgs'), [['org', 0, r => esc(r.org || '—') + ' <span class="muted">AS' + esc(r.asn || '?') + '</span>'], ['n', 1], ['ips', 1, r => fmtNumber(r.ips) + ' IPs']]) +
    table('Browser languages', arr('languages'), [['lang'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Device types', arr('devices'), [['dev'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Screen sizes (CSS px @ DPR)', arr('screens'), [['scr'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Device time zones · local vs Cloudflare', arr('deviceTimezones'), [['dtz', 0, r => esc(r.dtz || '—') + (r.tz && r.tz !== r.dtz ? '<br><span class="muted">IP timezone: ' + esc(r.tz) + '</span>' : (r.tz ? '<br><span class="muted">IP timezone agrees</span>' : ''))], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Android models (when reported)', arr('models'), [['model', 0, r => esc(modelName(r.model))], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Network · VPN / relay / timezone check', arr('networkFlags'), [['flag', 0, r => netTags([r.flag]) + ' <span class="muted">' + esc(NET_TITLES[r.flag] || '') + '</span>'], ['views', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Countries without VPN / relay views', arr('countriesClean'), [['country'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u'], ['hidden', 1, r => r.hidden ? '<span title="views from VPN / relay IPs in this country, left out">+' + fmtNumber(r.hidden) + ' VPN</span>' : '']]) +
    table('Approx. postal areas', arr('postals'), [['postal', 0, r => esc(r.postal || '—') + '<br><span class="muted">' + esc([r.city, r.region, r.country].filter(Boolean).join(', ')) + '</span>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    '<div class="card full engagement"><h2>Engagement · visible page time and sessions</h2><div class="engagement-grid">' + [
      [fmtNumber(engagement.measured), 'measured page views'],
      [fmtDuration(engagement.avg_seconds), 'average visible time / page'],
      [fmtDuration(engagement.max_seconds), 'longest visible time / page'],
      [fmtNumber(sessions.sessions), 'sessions · 30-min gap'],
      [fmtNumber(sessions.avg_pages), 'average pages / session'],
      [fmtNumber(sessions.max_pages), 'maximum pages / session'],
      [fmtDuration(sessions.avg_visible_seconds), 'average measured session time']
    ].map(([value, label]) => '<div class="engagement-stat"><b>' + esc(value) + '</b><span>' + esc(label) + '</span></div>').join('') + '</div></div>' +
    table('Sources · where sessions start', arr('sources'), [
      ['source', 0, r => '<span class="tag ' + esc(r.kind) + '">' + esc(r.kind) + '</span> ' + (r.kind === 'referrer' ? refHtml('https://' + r.source + '/') : esc(r.source))],
      ['sessions', 1, r => fmtNumber(r.sessions) + ' sess'], ['views', 1, r => fmtNumber(r.views)], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Off-site referrers', arr('externalRefs'), [['ref', 0, r => '<span title="' + esc(r.ref) + '">' + refHtml(r.ref) + '</span> <span class="muted">' + esc(r.ref) + '</span>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('UTM tags', arr('utms'), [['utm', 0, r => '<code>' + esc(r.utm) + '</code>'], ['n', 1], ['u', 1, r => fmtNumber(r.u) + ' u']]) +
    table('Sessions · page sequence (newest ' + arr('sessionList').length + ' of the last ' + fmtNumber(D.sessionRows) + ' views)', arr('sessionList'), [
      ['start', 0, r => '<span class="muted">' + when(r.start) + '</span><br><b>' + esc(fmtDuration(r.seconds)) + '</b>' +
        '<br><span class="muted">' + fmtNumber(r.views) + (r.views === 1 ? ' view' : ' views') + (r.visible_seconds != null ? ' · visible ' + esc(fmtDuration(r.visible_seconds)) : '') + '</span>'],
      ['who', 0, r => '<code>' + esc(String(r.who || '').slice(0, 6)) + '</code>' + (r.owner ? ' <span class="tag owner">owner</span>' : '') + ' ' + netTags(r.net) +
        ((r.vids || []).length > 1 ? ' <span class="tag" title="browser ids with the same fingerprint and IP: ' + esc(r.vids.join(', ')) + '">' + r.vids.length + ' browser ids</span>' : '') +
        '<br><span class="muted">' + esc([agent(r.ua), r.dev, deviceLabel(r), r.lang, r.dtz && r.dtz !== r.tz ? 'device TZ ' + r.dtz : ''].filter(Boolean).join(' · ')) +
        '<br>' + esc([r.city, r.country].filter(Boolean).join(', ')) + (r.org ? ' · ' + esc(r.org) : '') + '</span>'],
      ['source', 0, r => { const s = r.source || {}; return '<span class="tag ' + esc(s.kind) + '">' + esc(s.kind || '?') + '</span><br>' +
        (s.kind === 'referrer' ? '<span title="' + esc(s.ref) + '">' + refHtml(s.ref) + '</span>' : '<span class="muted">' + esc(s.kind === 'utm' ? s.utm : '') + '</span>'); }],
      ['pages', 0, r => '<div class="seq">' + (r.pages || []).map((pg, i) => (i ? '<i>→</i>' : '') +
        '<a href="' + esc(pg.path) + '" title="' + esc(when(pg.ts)) + '">' + esc(pg.path) + '</a>' +
        (pg.dur != null ? '<i>' + esc(fmtDuration(pg.dur)) + '</i>' : '')).join('') +
        (r.views > (r.pages || []).length ? '<i>… +' + (r.views - r.pages.length) + '</i>' : '') + '</div>']
    ], 'full') +
    table('IP addresses · visits per IP', arr('ipStats'), [
      ['ip', 0, r => '<code>' + esc(r.ip) + '</code><br><span class="muted">' + esc([r.city, r.region, r.country].filter(Boolean).join(', ')) + (r.postal ? ' · ZIP ' + esc(r.postal) : '') + '</span>'],
      ['views', 1, r => fmtNumber(r.views)],
      ['visits', 1, r => fmtNumber(r.visits)],
      ['fingerprints', 1, r => fmtNumber(r.fingerprints)],
      ['first_seen', 0, r => '<span class="muted">' + when(r.first_seen) + '</span>'],
      ['last_seen', 0, r => '<span class="muted">' + when(r.last_seen) + '</span>'],
      ['org', 0, r => esc(r.org || '—')]
    ], 'full') +
    table('Device fingerprints · coarse profile match', arr('fingerprints'), [
      ['fp', 0, r => '<code title="' + esc(r.fp) + '">' + esc((r.fp || '').slice(0, 12)) + '…</code><br><span class="muted">' + esc([r.dev, modelName(r.model), r.scr, r.lang, r.dtz].filter(Boolean).join(' · ')) + '</span>'],
      ['views', 1, r => fmtNumber(r.views)],
      ['ips', 1, r => fmtNumber(r.ips)],
      ['browser_ids', 1, r => fmtNumber(r.browser_ids)],
      ['first_seen', 0, r => '<span class="muted">' + when(r.first_seen) + '</span>'],
      ['last_seen', 0, r => '<span class="muted">' + when(r.last_seen) + '</span>'],
      ['country', 0]
    ], 'full') +
    table('Latest views', arr('recent'), [['ts', 0, r => '<span class="muted">' + when(r.ts) + '</span>'], ['path', 0, r => esc(r.path)],
      ['ip', 0, r => esc(r.ip) + '<br><span class="muted">' + esc([r.city, r.region, r.country].filter(Boolean).join(', ')) + (r.postal ? ' · ZIP ' + esc(r.postal) : '') + ' · ' + esc(r.org || '') + '</span>'],
      ['ua', 0, r => {
        const meta = [r.utm ? 'utm ' + r.utm : '', r.owner ? 'owner' : '',
          r.dev || '', deviceLabel(r), r.scr ? 'screen ' + r.scr : '',
          r.lang ? 'lang ' + r.lang : '', r.dtz ? 'device TZ ' + r.dtz : '',
          r.tz ? 'IP TZ ' + r.tz : '', r.dur !== null && r.dur !== undefined ? 'visible ' + fmtDuration(r.dur) : ''
        ].filter(Boolean).map(esc).join(' · ');
        return netTags(r.net) + ' <span class="muted">' + esc(agent(r.ua)) + (r.ref ? ' · from ' + refHtml(r.ref) : '') +
          ' · ' + esc((r.vid || '').slice(0, 6)) + (meta ? '<br>' + meta : '') + '</span>';
      }]], 'full') +
    '</div><p class="note">Kept 90 days. Times UTC. “u” = distinct visitors (browser id, or IP if storage is unavailable). A session groups page views separated by no more than 30 minutes; its duration runs from the first view to the end of the last one (that page’s visible time, when measured). Source = the session’s utm tags, else its off-site referrer; “internal” = it starts from one of this site’s pages (a reopened tab, or a visit begun before the window), “direct” = no referrer at all. Page time counts visible-tab time, not time left open in a background tab. Postal code is an approximate Cloudflare IP-derived area; client hints may omit the device model. Device fingerprints are SHA-256 signatures of already-collected browser metadata, not proof of a unique physical device. IP visit counts split activity into sessions after 30 minutes of inactivity. Network flags are heuristics: VPN = a known VPN / hosting network, relay = iCloud Private Relay / WARP, TZ≠IP = the device clock disagrees with the IP location (VPN or a traveller). Precise GPS location would need a permission prompt for every visitor and is not collected. People = browser ids merged when they share a device fingerprint and an IP (cleared storage, private window). New / returning: a visitor counts as new on the day of their first view kept. Now on site = views in the last 5 minutes. Scroll depth and Web Vitals come with the leave beacon of each view.</p>';
}
exportButton.onclick = () => {
  if (!exportData) return;
  const payload = JSON.parse(JSON.stringify(exportData));
  if (document.getElementById('mask-ips').checked) {
    const mask = ip => {
      if (!ip) return ip;
      if (String(ip).includes(':')) return String(ip).split(':').slice(0, 4).join(':') + '::';
      const parts = String(ip).split('.');
      return parts.length === 4 ? parts.slice(0, 3).join('.') + '.x' : ip;
    };
    for (const row of (payload.ipStats || [])) row.ip = mask(row.ip);
    for (const row of (payload.recent || [])) row.ip = mask(row.ip);
    for (const row of (payload.sessionList || [])) row.ip = mask(row.ip);
    for (const row of (payload.nowOnSite || [])) row.ip = mask(row.ip);
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '4sq-visits-' + days + 'd-' + new Date().toISOString().slice(0, 10) + '.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.getElementById('save').onclick = () => { const v = document.getElementById('key').value.trim(); if (v) { ls('fsq_stats_key', v); document.getElementById('keybar').hidden = true; load(); } };
document.getElementById('key').onkeydown = e => { if (e.key === 'Enter') document.getElementById('save').click(); };
document.getElementById('forget').onclick = () => { ls('fsq_stats_key', null); load(); };
document.getElementById('with-owner').onchange = load;
document.getElementById('clear').onclick = async () => {
  const key = ls('fsq_stats_key');
  if (!key) { load(); return; }
  const typed = prompt('Delete ALL recorded visits (every period, owner views too)? This cannot be undone — download the JSON first if you need it.\n\nType clear to confirm:');
  if (typed === null) return;
  if (typed.trim().toLowerCase() !== 'clear') { alert('Not cleared: the confirmation word did not match.'); return; }
  let r, D;
  try {
    r = await fetch('/api/visits', { method: 'POST', headers: { 'X-Stats-Key': encodeURIComponent(key), 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'clear', confirm: 'clear' }) });
    D = await r.json().catch(() => ({ error: 'bad response' }));
  } catch (e) { alert('Request failed: ' + (e.message || e)); return; }
  if (!r.ok || D.error) { alert('Not cleared: ' + (D.error || r.status)); return; }
  alert('Cleared ' + D.deleted + ' views' + (D.events ? ' and ' + D.events + ' events' : '') + '. Monitoring starts from zero.');
  load();
};
document.querySelectorAll('#ctl button[data-d]').forEach(b => b.onclick = () => { days = +b.dataset.d; document.querySelectorAll('#ctl button[data-d]').forEach(x => x.classList.toggle('on', x === b)); load(); });
load();
</script>
</body>
</html>"""


DEVICE_NAMES = Path(__file__).resolve().parents[1] / "config" / "device_names.json"


def build_page(out_path: str = "", **_extra) -> None:
    out_dir = Path(out_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "visits.html").write_text(PAGE, encoding="utf-8")
    try:
        names = json.loads(DEVICE_NAMES.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:   # no table: the page just shows raw model codes
        log.warning("device names unavailable (%s)", err)
        names = {}
    (out_dir / "device-names.json").write_text(json.dumps(names, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("visits.html written (private, STATS_KEY-gated), %d device names", len(names))
