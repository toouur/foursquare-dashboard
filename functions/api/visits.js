// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Private aggregates for the owner's visits.html, backed by /api/hit.
 * Guarded by STATS_KEY; no-store; always return structured JSON errors.
 */
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

function sameKey(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export async function onRequestGet({ request, env }) {
  const sent = request.headers.get('X-Stats-Key') || '';
  if (!env.STATS_KEY || !(sameKey(sent, encodeURIComponent(env.STATS_KEY)) || sameKey(sent, env.STATS_KEY))) {
    return json({ error: 'unauthorized' }, 401);
  }
  if (!env.DB) return json({ error: 'D1 unavailable' }, 503);

  try {
    const url = new URL(request.url);
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const since = Math.floor(Date.now() / 1000) - days * 86400;
    const own = url.searchParams.get('owner') === '1' ? '' : ' AND owner = 0';
    const W = 'WHERE ts >= ?' + own;
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
      engagement, sessions,
    ] = await Promise.all([
      q('SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors, COUNT(DISTINCT ip) AS ips FROM visits ' + W),
      q("SELECT date(ts, 'unixepoch') AS d, COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors FROM visits " + W + ' GROUP BY d ORDER BY d'),
      q('SELECT path, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY path ORDER BY n DESC LIMIT 40'),
      q('SELECT country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY country ORDER BY n DESC LIMIT 40'),
      q('SELECT city, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W + ' GROUP BY city, country ORDER BY n DESC LIMIT 40'),
      q('SELECT ref, COUNT(*) AS n FROM visits ' + W + " AND ref IS NOT NULL AND ref != '' GROUP BY ref ORDER BY n DESC LIMIT 40"),
      q('SELECT org, asn, COUNT(*) AS n, COUNT(DISTINCT ip) AS ips FROM visits ' + W + ' GROUP BY org, asn ORDER BY n DESC LIMIT 30'),
      q('SELECT ts, path, ref, ip, country, city, region, asn, org, tz, ua, vid, owner, lang, dtz, scr, dev, model, postal, dur FROM visits ' + W + ' ORDER BY ts DESC LIMIT 200'),
      top('lang', 12),
      top('dev', 8),
      top('scr', 12),
      q('SELECT dtz, tz, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND dtz IS NOT NULL AND dtz != '' GROUP BY dtz, tz ORDER BY n DESC LIMIT 12"),
      top('model', 12),
      q('SELECT postal, city, region, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ' + W +
        " AND postal IS NOT NULL AND postal != '' GROUP BY postal, city, region, country ORDER BY n DESC LIMIT 20"),
      q('SELECT COUNT(dur) AS measured, ROUND(AVG(dur), 0) AS avg_seconds, MAX(dur) AS max_seconds FROM visits ' + W + ' AND dur IS NOT NULL'),
      q(sessionSql),
    ]);
    return json({
      days,
      totals: tot[0] || {}, daily, paths, countries, cities, refs, orgs, recent,
      languages, devices, screens, deviceTimezones, models, postals,
      engagement: engagement[0] || {},
      sessions: sessions[0] || {},
    });
  } catch (e) {
    return json({ error: 'D1 query failed', detail: String(e && e.message || e) }, 503);
  }
}
