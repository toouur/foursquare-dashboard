// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/health
 *
 * Lightweight health probe for uptime monitors and CI:
 *   d1     — D1 binding present and answering; reports row count and the age
 *            of the newest check-in (data-freshness signal: the hourly fetch
 *            job normally keeps this under a day or two).
 *   assets — static build artefacts served (feed_meta.json parses; its total
 *            should track the D1 row count).
 *
 * Returns 200 with status "ok", or 503 with status "degraded" and a per-check
 * error. Never cached.
 */

const HEADERS = {
  'Content-Type': 'application/json',
  'Cache-Control': 'no-store',
};

export async function onRequestGet({ request, env }) {
  const checks = {};

  try {
    if (!env.DB) throw new Error('DB binding not configured');
    const row = await env.DB
      .prepare('SELECT COUNT(*) AS n, MAX(ts) AS latest FROM checkins')
      .first();
    checks.d1 = {
      ok: true,
      checkins: row.n,
      latest_checkin_age_h: row.latest
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
  return new Response(
    JSON.stringify({
      status: ok ? 'ok' : 'degraded',
      time: new Date().toISOString(),
      checks,
    }),
    { status: ok ? 200 : 503, headers: HEADERS },
  );
}
