// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Private aggregates for the owner's visits.html, backed by /api/hit.
 * Guarded by STATS_KEY; no-store; always return structured JSON errors.
 * POST {action:'clear', confirm:'clear'} (same key) deletes every view and event
 * recorded so far, so the owner can start monitoring from zero.
 * Queries on newer tables/columns (visit_events, scroll, vitals, venues) degrade
 * to empty lists, so a not-yet-migrated database still renders the rest.
 */
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const SESSION_GAP = 1800;      // the same 30-minute rule as the SQL aggregates
const SESSION_ROWS = 3000;     // newest views grouped into the session list
const SESSION_LIST = 60;       // sessions returned, newest first
const SESSION_PAGES = 80;      // pages kept per session

// ── Network flags: heuristics read from what every row already has, never proof ──
//   vpn    the IP belongs to a VPN / hosting / datacenter network (ASN list or org name)
//   relay  a privacy relay egress (iCloud Private Relay, Cloudflare WARP: Cloudflare,
//          Akamai, Fastly) — the real place is hidden, the country is usually kept
//   tz     the device clock's UTC offset differs from the IP location's: a VPN, or a
//          traveller / a phone set to another country (residential ISPs too)
const VPN_ASNS = new Set([
  9009, 60068, 212238,            // M247, Datacamp/CDN77 — most commercial VPN exits
  136787, 147049, 207137,         // Tefincom, PacketHub (NordVPN)
  39351, 209854,                  // 31173 Services (Mullvad), Cyberzone (Surfshark)
  16509, 14618, 15169, 396982, 8075, 31898, 45102, 132203,   // AWS, Google, Microsoft, Oracle, Alibaba, Tencent
  14061, 24940, 213230, 16276, 63949, 20473, 51167, 60781, 28753, 62240, 49981, 202425,
  // DigitalOcean, Hetzner, OVH, Linode, Vultr, Contabo, Leaseweb, Clouvider, WorldStream, IP Volume
]);
const RELAY_ASNS = new Set([13335, 36183, 20940, 16625, 54113]);   // Cloudflare, Akamai ×3, Fastly
const VPN_ORG_RE = /vpn|hosting|datacent|data cent|\bvps\b|colocat|m247|datacamp|cdn77|hetzner|\bovh|digitalocean|linode|vultr|choopa|contabo|leaseweb|packethub|tefincom|proton|mullvad|amazon|google cloud|microsoft|oracle|alibaba|tencent/i;
const offsetFmt = new Map();
function tzOffset(zone, ts) {
  if (!zone) return null;
  try {
    let f = offsetFmt.get(zone);
    if (!f) { f = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'shortOffset' }); offsetFmt.set(zone, f); }
    const part = f.formatToParts(new Date(ts * 1000)).find(x => x.type === 'timeZoneName');
    return part ? part.value : null;
  } catch { return null; }
}
function netFlags(r) {
  const flags = [];
  const asn = Number(r.asn);
  if (RELAY_ASNS.has(asn)) flags.push('relay');
  else if (VPN_ASNS.has(asn) || (r.org && VPN_ORG_RE.test(r.org))) flags.push('vpn');
  if (r.dtz && r.tz && r.dtz !== r.tz) {
    const a = tzOffset(r.dtz, r.ts), b = tzOffset(r.tz, r.ts);
    if (a && b && a !== b) flags.push('tz');
  }
  return flags;
}

// Views per flag, and countries counted without the views whose IP hides the real place.
function netSummary(rows) {
  const flagTally = new Map(), ctry = new Map();
  for (const r of rows) {
    const who = r.vid || r.ip || 'unknown-' + r.id;
    const flags = netFlags(r);
    // 'clean' needs the device timezone; rows without it (older beacon) only had the network check
    for (const f of flags.length ? flags : [r.dtz ? 'clean' : 'no-tz']) {
      const t = flagTally.get(f) || { flag: f, views: 0, who: new Set() };
      t.views += 1; t.who.add(who); flagTally.set(f, t);
    }
    const c = r.country || '—';
    const t = ctry.get(c) || { country: c, n: 0, hidden: 0, who: new Set() };
    if (flags.includes('vpn') || flags.includes('relay')) t.hidden += 1;
    else { t.n += 1; t.who.add(who); }
    ctry.set(c, t);
  }
  return {
    networkFlags: [...flagTally.values()].map(t => ({ flag: t.flag, views: t.views, u: t.who.size })).sort((a, b) => b.views - a.views),
    countriesClean: [...ctry.values()].map(t => ({ country: t.country, n: t.n, u: t.who.size, hidden: t.hidden }))
      .sort((a, b) => b.n - a.n || b.hidden - a.hidden),
  };
}

// Browser ids that share a device fingerprint AND an IP are one person who cleared
// storage or used a private window: map each id to the earliest one of its group.
function personMap(rows) {
  const parent = new Map();
  const find = (x) => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
  const first = new Map(), byKey = new Map();
  for (const r of rows) {
    if (!r.vid) continue;
    if (!parent.has(r.vid)) parent.set(r.vid, r.vid);
    first.set(r.vid, Math.min(first.get(r.vid) ?? Infinity, r.ts));
    if (r.fp && r.ip) {
      const key = r.fp + '|' + r.ip, other = byKey.get(key);
      if (!other) byKey.set(key, r.vid);
      else {
        const a = find(other), b = find(r.vid);
        if (a !== b) { if (first.get(a) <= first.get(b)) parent.set(b, a); else parent.set(a, b); }
      }
    }
  }
  const out = new Map();
  for (const v of parent.keys()) out.set(v, find(v));
  return out;
}

// 75th percentile per page (and over all pages) of each Web Vital.
const VITALS = ['lcp', 'inp', 'cls', 'ttfb'];
function p75(values) {
  if (!values.length) return null;
  const s = values.slice().sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.ceil(0.75 * s.length) - 1)];
}
function vitalsTable(rows) {
  const groups = new Map([['(all pages)', []]]);
  for (const r of rows) {
    groups.get('(all pages)').push(r);
    if (!groups.has(r.path)) groups.set(r.path, []);
    groups.get(r.path).push(r);
  }
  return [...groups.entries()].map(([path, list]) => {
    const row = { path, views: list.length };
    for (const k of VITALS) {
      const vals = list.map(x => x[k]).filter(x => x !== null && x !== undefined);
      row[k] = p75(vals); row[k + '_n'] = vals.length;
    }
    return row;
  }).sort((a, b) => (a.path === '(all pages)' ? -1 : b.path === '(all pages)' ? 1 : b.views - a.views)).slice(0, 25);
}

// Who is on the site: views in the last 5 minutes, one line per visitor.
function nowOnSite(rows) {
  const by = new Map();
  for (const r of rows) {   // newest first
    const who = r.vid || r.ip || 'unknown-' + r.id;
    const cur = by.get(who);
    if (!cur) by.set(who, { who, last: r.ts, path: r.path, views: 1, city: r.city, country: r.country, dev: r.dev, model: r.model, ua: r.ua, scr: r.scr, owner: r.owner });
    else cur.views += 1;
  }
  return [...by.values()];
}

const hostOf = (u) => { try { return new URL(u).host; } catch { return null; } };

// Where a session came from: its utm tags, else an off-site referrer, else
// 'internal' (it starts on a page reached from this site: the visit began
// before the window, or the tab was reopened) or 'direct' (typed/bookmark/app).
function sessionSource(views, ownHost) {
  const tagged = views.find(v => v.utm);
  if (tagged) {
    const tags = Object.fromEntries(new URLSearchParams(tagged.utm));
    return { kind: 'utm', label: [tags.source, tags.medium, tags.campaign].filter(Boolean).join(' / ') || tagged.utm, utm: tagged.utm };
  }
  const ext = views.find(v => v.ref && hostOf(v.ref) !== ownHost);
  if (ext) return { kind: 'referrer', label: hostOf(ext.ref) || ext.ref, ref: ext.ref };
  return views[0].ref ? { kind: 'internal', label: 'internal' } : { kind: 'direct', label: 'direct' };
}

// rows: newest first. Returns the newest sessions plus a per-source tally over all of them.
function buildSessions(rows, ownHost) {
  const open = new Map(), all = [], person = personMap(rows);
  for (const r of rows.slice().reverse()) {
    const who = (r.vid && person.get(r.vid)) || r.vid || r.ip || 'unknown-' + r.id;
    let s = open.get(who);
    if (!s || r.ts - s.end > SESSION_GAP) {
      s = { who, start: r.ts, end: r.ts, views: [] };
      open.set(who, s);
      all.push(s);
    }
    s.end = r.ts;
    s.views.push(r);
  }
  const sessions = all.map(s => {
    const v = s.views, last = v[v.length - 1], pick = k => (v.find(x => x[k]) || {})[k] || null;
    const measured = v.filter(x => x.dur !== null && x.dur !== undefined);
    return {
      who: s.who, start: s.start, end: s.end,
      seconds: s.end - s.start + (Number(last.dur) || 0),
      visible_seconds: measured.length ? measured.reduce((a, x) => a + Number(x.dur), 0) : null,
      views: v.length,
      pages: v.slice(0, SESSION_PAGES).map(x => ({ ts: x.ts, path: x.path, dur: x.dur })),
      source: sessionSource(v, ownHost),
      ip: pick('ip'), city: pick('city'), country: pick('country'), org: pick('org'),
      dev: pick('dev'), model: pick('model'), lang: pick('lang'), ua: pick('ua'), scr: pick('scr'),
      dtz: pick('dtz'), tz: pick('tz'),
      net: [...new Set(v.flatMap(netFlags))],
      vids: [...new Set(v.map(x => x.vid).filter(Boolean))],
      owner: v.some(x => x.owner) ? 1 : 0,
    };
  });
  const tally = new Map();
  for (const s of sessions) {
    const key = s.source.kind + '\u0000' + s.source.label;
    const t = tally.get(key) || { kind: s.source.kind, source: s.source.label, sessions: 0, views: 0, who: new Set() };
    t.sessions += 1; t.views += s.views; t.who.add(s.who);
    tally.set(key, t);
  }
  const sources = [...tally.values()]
    .map(t => ({ kind: t.kind, source: t.source, sessions: t.sessions, views: t.views, u: t.who.size }))
    .sort((a, b) => b.sessions - a.sessions || b.views - a.views);
  const merged = new Set([...person.entries()].filter(([v, root]) => v !== root).map(([v]) => v)).size;
  return { list: sessions.sort((a, b) => b.start - a.start).slice(0, SESSION_LIST), sources, rows: rows.length,
    people: new Set(all.map(s => s.who)).size, merged };
}

function sameKey(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function authorized(request, env) {
  const sent = request.headers.get('X-Stats-Key') || '';
  return !!env.STATS_KEY && (sameKey(sent, encodeURIComponent(env.STATS_KEY)) || sameKey(sent, env.STATS_KEY));
}

export async function onRequestPost({ request, env }) {
  if (!authorized(request, env)) return json({ error: 'unauthorized' }, 401);
  if (!env.DB) return json({ error: 'D1 unavailable' }, 503);
  let body = {};
  try { body = JSON.parse(await request.text()); } catch { body = {}; }
  // the literal confirm word keeps a stray or replayed request from wiping the table
  if (body.action !== 'clear' || body.confirm !== 'clear') return json({ error: 'expected {action:"clear", confirm:"clear"}' }, 400);
  try {
    const now = Math.floor(Date.now() / 1000);
    const r = await env.DB.prepare('DELETE FROM visits WHERE ts <= ?').bind(now).run();
    const e = await env.DB.prepare('DELETE FROM visit_events WHERE ts <= ?').bind(now).run().catch(() => null);
    return json({ deleted: (r && r.meta && r.meta.changes) || 0, events: (e && e.meta && e.meta.changes) || 0, before: now });
  } catch (e) {
    return json({ error: 'D1 delete failed', detail: String(e && e.message || e) }, 503);
  }
}

export async function onRequestGet({ request, env }) {
  if (!authorized(request, env)) return json({ error: 'unauthorized' }, 401);
  if (!env.DB) return json({ error: 'D1 unavailable' }, 503);

  try {
    const url = new URL(request.url);
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const since = Math.floor(Date.now() / 1000) - days * 86400;
    const own = url.searchParams.get('owner') === '1' ? '' : ' AND owner = 0';
    const W = 'WHERE ts >= ?' + own;
    const ownHost = url.host;
    const q = (sql) => env.DB.prepare(sql).bind(since).all().then(r => r.results || []);
    const soft = (sql, ...args) => env.DB.prepare(sql).bind(...args).all().then(r => r.results || []).catch(() => []);
    const now = Math.floor(Date.now() / 1000);
    const top = (column, limit = 12) => q(
      'SELECT ' + column + ', COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u ' +
      'FROM visits ' + W + ' AND ' + column + " IS NOT NULL AND " + column + " != '' " +
      'GROUP BY ' + column + ' ORDER BY n DESC LIMIT ' + Number(limit)
    );

    // One session = consecutive views by the same visitor with no gap over 30 minutes.
    const sessionSql =
      'WITH base AS (' +
        "SELECT id, ts, COALESCE(vid, ip, 'unknown-' || id) AS who, dur FROM visits " + W +
      '), lagged AS (' +
        'SELECT *, LAG(ts) OVER (PARTITION BY who ORDER BY ts, id) AS prev_ts FROM base' +
      '), marked AS (' +
        'SELECT *, CASE WHEN prev_ts IS NULL OR ts - prev_ts > 1800 THEN 1 ELSE 0 END AS starts FROM lagged' +
      '), numbered AS (' +
        'SELECT *, SUM(starts) OVER (PARTITION BY who ORDER BY ts, id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS session_no FROM marked' +
      '), grouped AS (' +
        'SELECT who, session_no, COUNT(*) AS pages, SUM(CASE WHEN dur IS NULL THEN 0 ELSE dur END) AS visible_seconds, ' +
        'COUNT(dur) AS measured_pages FROM numbered GROUP BY who, session_no' +
      ') SELECT COUNT(*) AS sessions, ROUND(AVG(pages), 2) AS avg_pages, MAX(pages) AS max_pages, ' +
        'ROUND(AVG(CASE WHEN measured_pages > 0 THEN visible_seconds END), 0) AS avg_visible_seconds FROM grouped';

    const [
      tot, daily, paths, countries, cities, refs, orgs, recent,
      languages, devices, screens, deviceTimezones, models, postals,
      ipStats, fingerprints, engagement, sessions, sessionRows, externalRefs, utms,
      venueViews, searches, outbound, scrollDepth, vitalRows, nowRows, newReturning,
    ] = await Promise.all([
      q('SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors, COUNT(DISTINCT ip) AS ips FROM visits ' + W),
      q("SELECT date(ts, 'unixepoch') AS d, COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors FROM visits " + W + ' GROUP BY d ORDER BY d'),
      q('SELECT path, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY path ORDER BY n DESC LIMIT 40'),
      q('SELECT country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY country ORDER BY n DESC LIMIT 40'),
      q('SELECT city, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY city, country ORDER BY n DESC LIMIT 40'),
      q('SELECT ref, COUNT(*) AS n FROM visits ' + W + " AND ref IS NOT NULL AND ref != '' GROUP BY ref ORDER BY n DESC LIMIT 40"),
      q('SELECT org, asn, COUNT(*) AS n, COUNT(DISTINCT ip) AS ips FROM visits ' + W + ' GROUP BY org, asn ORDER BY n DESC LIMIT 30'),
      q('SELECT ts, path, ref, utm, ip, country, city, region, asn, org, tz, ua, vid, owner, lang, dtz, scr, dev, model, postal, dur FROM visits ' + W + ' ORDER BY ts DESC LIMIT 200'),
      top('lang', 12),
      top('dev', 8),
      top('scr', 12),
      q('SELECT dtz, tz, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND dtz IS NOT NULL AND dtz != '' GROUP BY dtz, tz ORDER BY n DESC LIMIT 12"),
      top('model', 12),
      q('SELECT postal, city, region, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND postal IS NOT NULL AND postal != '' GROUP BY postal, city, region, country ORDER BY n DESC LIMIT 20"),
      q('WITH base AS (SELECT id, ts, ip, city, region, country, postal, org, fp, vid FROM visits ' + W +
        " AND ip IS NOT NULL AND ip != ''), lagged AS (SELECT *, LAG(ts) OVER (PARTITION BY ip ORDER BY ts, id) AS prev_ts FROM base) " +
        "SELECT ip, COUNT(*) AS views, SUM(CASE WHEN prev_ts IS NULL OR ts - prev_ts > 1800 THEN 1 ELSE 0 END) AS visits, " +
        "COUNT(DISTINCT fp) AS fingerprints, COUNT(DISTINCT vid) AS browser_ids, MIN(ts) AS first_seen, MAX(ts) AS last_seen, " +
        "MAX(city) AS city, MAX(region) AS region, MAX(country) AS country, MAX(postal) AS postal, MAX(org) AS org " +
        "FROM lagged GROUP BY ip ORDER BY views DESC, last_seen DESC LIMIT 100"),
      q('SELECT fp, COUNT(*) AS views, COUNT(DISTINCT ip) AS ips, COUNT(DISTINCT vid) AS browser_ids, ' +
        'MIN(ts) AS first_seen, MAX(ts) AS last_seen, MAX(dev) AS dev, MAX(model) AS model, MAX(scr) AS scr, ' +
        'MAX(lang) AS lang, MAX(dtz) AS dtz, MAX(country) AS country FROM visits ' + W +
        " AND fp IS NOT NULL AND fp != '' GROUP BY fp ORDER BY views DESC, last_seen DESC LIMIT 100"),
      q('SELECT COUNT(dur) AS measured, ROUND(AVG(dur), 0) AS avg_seconds, MAX(dur) AS max_seconds FROM visits ' + W + ' AND dur IS NOT NULL'),
      q(sessionSql),
      q('SELECT id, ts, path, ref, utm, ip, city, country, asn, org, tz, dtz, scr, fp, vid, owner, dev, model, lang, ua, dur FROM visits ' + W +
        ' ORDER BY ts DESC, id DESC LIMIT ' + SESSION_ROWS),
      // off-site referrers only: links between our own pages are navigation, not a source
      env.DB.prepare('SELECT ref, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND ref IS NOT NULL AND ref != '' AND ref NOT LIKE ? GROUP BY ref ORDER BY n DESC LIMIT 40")
        .bind(since, url.origin + '/%').all().then(r => r.results || []),
      top('utm', 30),
      soft("SELECT substr(path, instr(path, '#') + 1) AS id, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits " + W +
        " AND path LIKE '/venue%#%' GROUP BY id ORDER BY n DESC LIMIT 30", since),
      soft('SELECT data, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u, MAX(ts) AS last FROM visit_events ' + W +
        " AND kind = 'search' GROUP BY data ORDER BY n DESC, last DESC LIMIT 40", since),
      soft('SELECT data, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u, MAX(ts) AS last FROM visit_events ' + W +
        " AND kind = 'out' GROUP BY data ORDER BY n DESC, last DESC LIMIT 40", since),
      soft('SELECT path, COUNT(scroll) AS measured, ROUND(AVG(scroll), 0) AS avg_scroll, ' +
        'SUM(CASE WHEN scroll >= 90 THEN 1 ELSE 0 END) AS reached_end FROM visits ' + W +
        ' AND scroll IS NOT NULL GROUP BY path ORDER BY measured DESC LIMIT 30', since),
      soft('SELECT path, lcp, cls, inp, ttfb FROM visits ' + W +
        ' AND (lcp IS NOT NULL OR inp IS NOT NULL OR cls IS NOT NULL OR ttfb IS NOT NULL) ORDER BY ts DESC LIMIT 3000', since),
      soft('SELECT id, ts, path, vid, ip, city, country, dev, model, ua, scr, owner FROM visits WHERE ts >= ?' + own +
        ' ORDER BY ts DESC LIMIT 200', now - 300),
      // a visitor is "new" on the day of their first view kept (90 days), "returning" after it
      soft('WITH f AS (SELECT COALESCE(vid, ip) AS who, MIN(ts) AS first FROM visits WHERE 1 = 1' + own + ' GROUP BY who), ' +
        "d AS (SELECT date(ts, 'unixepoch') AS d, COALESCE(vid, ip) AS who FROM visits " + W + ' GROUP BY d, who) ' +
        "SELECT d.d AS d, SUM(CASE WHEN date(f.first, 'unixepoch') = d.d THEN 1 ELSE 0 END) AS new, " +
        "SUM(CASE WHEN date(f.first, 'unixepoch') < d.d THEN 1 ELSE 0 END) AS returning " +
        'FROM d JOIN f ON f.who = d.who GROUP BY d.d ORDER BY d.d', since),
    ]);
    // venue names from the venues table (same database), for the venue ids viewed
    const ids = venueViews.map(v => v.id).filter(Boolean);
    const names = ids.length
      ? await soft('SELECT id, name, category, city, country FROM venues WHERE id IN (' + ids.map(() => '?').join(', ') + ')', ...ids)
      : [];
    const byId = new Map(names.map(n => [n.id, n]));
    const topVenues = venueViews.map(v => ({ ...v, ...(byId.get(v.id) || {}) }));
    const sessionData = buildSessions(sessionRows, ownHost);
    const net = netSummary(sessionRows);
    for (const r of recent) r.net = netFlags(r);
    return json({
      days,
      totals: tot[0] || {}, daily, paths, countries, cities, refs, orgs, recent,
      languages, devices, screens, deviceTimezones, models, postals, ipStats, fingerprints,
      engagement: engagement[0] || {},
      sessions: sessions[0] || {},
      sessionList: sessionData.list, sources: sessionData.sources, sessionRows: sessionData.rows,
      externalRefs, utms, networkFlags: net.networkFlags, countriesClean: net.countriesClean,
      people: sessionData.people, mergedBrowsers: sessionData.merged,
      topVenues, searches, outbound, scrollDepth, vitals: vitalsTable(vitalRows),
      nowOnSite: nowOnSite(nowRows), newReturning,
    });
  } catch (e) {
    return json({ error: 'D1 query failed', detail: String(e && e.message || e) }, 503);
  }
}
