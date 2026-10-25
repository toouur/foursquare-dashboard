// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Private aggregates for the owner's visits.html, backed by /api/hit.
 * Guarded by STATS_KEY; no-store; always return structured JSON errors.
 * POST {action:'clear', confirm:'clear'} (same key) deletes every view recorded
 * so far, so the owner can start monitoring from zero.
 */
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

const SESSION_GAP = 1800;      // the same 30-minute rule as the SQL aggregates
const SESSION_ROWS = 3000;     // newest views grouped into the session list
const SESSION_LIST = 60;       // sessions returned, newest first
const SESSION_PAGES = 80;      // pages kept per session

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
  const open = new Map(), all = [];
  for (const r of rows.slice().reverse()) {
    const who = r.vid || r.ip || 'unknown-' + r.id;
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
      dev: pick('dev'), model: pick('model'), lang: pick('lang'), ua: pick('ua'),
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
  return { list: sessions.sort((a, b) => b.start - a.start).slice(0, SESSION_LIST), sources, rows: rows.length };
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
    return json({ deleted: (r && r.meta && r.meta.changes) || 0, before: now });
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
      q('SELECT id, ts, path, ref, utm, ip, city, country, org, vid, owner, dev, model, lang, ua, dur FROM visits ' + W +
        ' ORDER BY ts DESC, id DESC LIMIT ' + SESSION_ROWS),
      // off-site referrers only: links between our own pages are navigation, not a source
      env.DB.prepare('SELECT ref, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND ref IS NOT NULL AND ref != '' AND ref NOT LIKE ? GROUP BY ref ORDER BY n DESC LIMIT 40")
        .bind(since, url.origin + '/%').all().then(r => r.results || []),
      top('utm', 30),
    ]);
    const sessionData = buildSessions(sessionRows, ownHost);
    return json({
      days,
      totals: tot[0] || {}, daily, paths, countries, cities, refs, orgs, recent,
      languages, devices, screens, deviceTimezones, models, postals, ipStats, fingerprints,
      engagement: engagement[0] || {},
      sessions: sessions[0] || {},
      sessionList: sessionData.list, sources: sessionData.sources, sessionRows: sessionData.rows,
      externalRefs, utms,
    });
  } catch (e) {
    return json({ error: 'D1 query failed', detail: String(e && e.message || e) }, 503);
  }
}
