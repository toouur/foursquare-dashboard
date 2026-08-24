// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/health
 *
 * Lightweight health probe for uptime monitors and CI:
 *   d1     — D1 binding present and answering; reports the age of the newest
 *            check-in (data-freshness signal: the hourly fetch job normally
 *            keeps this under a day or two).
 *   assets — static build artefacts served (feed_meta.json parses; its total
 *            is the check-in count, generated at build from the same CSV D1
 *            is synced from).
 *
 * Returns 200 with status "ok", or 503 with status "degraded" and a per-check
 * error.
 *
 * Row-budget notes (D1 free tier bills reads as rows SCANNED):
 *   - The query asks for MAX(date) only. It used to also ask COUNT(*), which
 *     scans all ~70k rows — every single probe. MAX() over the indexed `date`
 *     column is an index lookup (~1 row). The count now comes from
 *     feed_meta.json, which this function fetches anyway and which is derived
 *     from the same source CSV.
 *   - A 200 is stored in the edge cache for 60s. Pages Functions are NOT
 *     edge-cached by Cache-Control alone — a dynamic response needs the
 *     explicit Cache API — so an every-30s monitor otherwise hit D1 on every
 *     poll from every PoP. A degraded (503) response is never cached, so an
 *     outage is reported the moment it starts and clears the moment it ends.
 *     Any distinct query string is a distinct cache key, so `?fresh=1` forces
 *     an uncached probe.
 */

const CACHE_TTL = 60;

const HEADERS_OK = {
  'Content-Type': 'application/json',
  'Cache-Control': `public, max-age=0, s-maxage=${CACHE_TTL}`,
};
const HEADERS_BAD = {
  'Content-Type': 'application/json',
  'Cache-Control': 'no-store',
};

export async function onRequestGet({ request, env, waitUntil }) {
  const cache = caches.default;
  const hit = await cache.match(request);
  if (hit) return hit;

  const checks = {};

  try {
    if (!env.DB) throw new Error('DB binding not configured');
    const row = await env.DB
      .prepare('SELECT MAX(date) AS latest FROM checkins')
      .first();
    checks.d1 = {
      ok: true,
      latest_checkin_age_h: row && row.latest
        ? Math.round((Date.now() / 1000 - row.latest) / 36) / 100
        : null,
    };
  } catch (e) {
    checks.d1 = { ok: false, error: String(e.message || e) };
  }

  try {
    const res = await env.ASSETS.fetch(new URL('/feed_meta.json', request.url));
    if (!res.ok) throw new Error(`feed_meta.json HTTP ${res.status}`);
    const meta = await res.json();
    checks.assets = { ok: true, feed_total: meta.total ?? null };
  } catch (e) {
    checks.assets = { ok: false, error: String(e.message || e) };
  }

  const ok = Object.values(checks).every(c => c.ok);
  const resp = new Response(
    JSON.stringify({
      status: ok ? 'ok' : 'degraded',
      time: new Date().toISOString(),
      checks,
    }),
    { status: ok ? 200 : 503, headers: ok ? HEADERS_OK : HEADERS_BAD },
  );
  if (ok && waitUntil) waitUntil(cache.put(request, resp.clone()));
  return resp;
}
