// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — GET /api/visits?days=30[&owner=1]
 *
 * Private aggregates over the `visits` table written by /api/hit, for
 * visits.html. Gated by the STATS_KEY Pages secret, sent as the X-Stats-Key
 * header (visits.html asks for it once and keeps it in localStorage). No key
 * configured or a wrong key => 401, so the endpoint is closed by default.
 * Cloudflare Access on /visits.html + /api/visits is an optional second lock.
 *
 * The owner's own views (owner = 1) are excluded unless ?owner=1.
 * Never cached; never throws (503 with {error} when D1 fails).
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
  if (!env.STATS_KEY || !sameKey(request.headers.get('X-Stats-Key') || '', env.STATS_KEY)) {
    return json({ error: 'unauthorized' }, 401);
  }
  if (!env.DB) return json({ error: 'D1 unavailable' }, 503);
  try {
    const url = new URL(request.url);
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const since = Math.floor(Date.now() / 1000) - days * 86400;
    const own = url.searchParams.get('owner') === '1' ? '' : ' AND owner = 0';
    const W = `WHERE ts >= ?${own}`;
    const q = (sql) => env.DB.prepare(sql).bind(since).all().then(r => r.results || []);
    const [tot, daily, paths, countries, cities, refs, orgs, recent] = await Promise.all([
      q(`SELECT COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors, COUNT(DISTINCT ip) AS ips FROM visits ${W}`),
      q(`SELECT date(ts, 'unixepoch') AS d, COUNT(*) AS views, COUNT(DISTINCT COALESCE(vid, ip)) AS visitors FROM visits ${W} GROUP BY d ORDER BY d`),
      q(`SELECT path, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ${W} GROUP BY path ORDER BY n DESC LIMIT 40`),
      q(`SELECT country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ${W} GROUP BY country ORDER BY n DESC LIMIT 40`),
      q(`SELECT city, country, COUNT(*) AS n, COUNT(DISTINCT COALESCE(vid, ip)) AS u FROM visits ${W} GROUP BY city, country ORDER BY n DESC LIMIT 40`),
      q(`SELECT ref, COUNT(*) AS n FROM visits ${W} AND ref IS NOT NULL AND ref != '' GROUP BY ref ORDER BY n DESC LIMIT 40`),
      q(`SELECT org, asn, COUNT(*) AS n, COUNT(DISTINCT ip) AS ips FROM visits ${W} GROUP BY org, asn ORDER BY n DESC LIMIT 30`),
      q(`SELECT ts, path, ref, ip, country, city, region, org, ua, vid, owner FROM visits ${W} ORDER BY ts DESC LIMIT 200`),
    ]);
    return json({ days, totals: tot[0] || {}, daily, paths, countries, cities, refs, orgs, recent });
  } catch (e) {
    return json({ error: 'D1 query failed', detail: String(e && e.message || e) }, 503);
  }
}
