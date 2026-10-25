// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — POST /api/hit
 *
 * Own page-view counter (Cloudflare Web Analytics has no IPs, cities or unique
 * visitors). Every page sends one navigator.sendBeacon('/api/hit', {p, r, v, o})
 * — the snippet is injected by build.py next to the Web Analytics beacon:
 *   p  path (+ #hash on venue.html, where the hash IS the page)
 *   r  document.referrer
 *   v  random visitor id from localStorage (no cookie)
 *   o  1 when this browser belongs to the owner (set by opening visits.html)
 *   l  navigator.language · z  the device's timezone · s  screen "WxH@dpr"
 *   d  phone / tablet / desktop · m  Android model from client hints (if given)
 * When the page is hidden a second beacon {t:'leave', p, v, dur} writes the
 * seconds it was visible onto that view's row (no new row).
 * IP, country, city, region, ASN/organisation and timezone come from the
 * request itself (CF-Connecting-IP + request.cf), never from the client.
 *
 * Always answers 204 and never throws: a counter must not break a page, and an
 * error here must not surface as a 1101. Bots (by User-Agent) and requests
 * from another origin are dropped.
 * Retention: 1 in 200 hits also deletes rows older than 90 days (the weekly
 * report does the same), so the table stays bounded even if CI is paused.
 * Abuse: the origin check only stops honest browsers and pages.dev gets no WAF
 * rate-limit rule (not our zone), so the visitor id must look like one the
 * snippet makes and one IP gets at most MAX_PER_IP views per 10 minutes — one
 * indexed read per hit (reads are the cheap budget, writes are not).
 * Writes per view: the row + the ts index = 2 rows written.
 */

const RETENTION_S = 90 * 86400;
const MAX_PER_IP = 60;          // views per IP per 10 minutes
const VID_RE = /^[a-z0-9]{8,40}$/;
const BOT_RE = /bot|crawl|spider|slurp|headless|lighthouse|pagespeed|preview|fetch|monitor|uptime|python|curl|wget|axios|node-fetch|playwright|puppeteer/i;
const noContent = () => new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });

const clip = (s, n) => (typeof s === 'string' ? s.slice(0, n) : null);

export async function onRequestPost({ request, env }) {
  try {
    if (!env.DB) return noContent();
    // same-origin only: a public endpoint that writes rows is otherwise a free
    // way to burn the D1 daily write budget
    const origin = request.headers.get('Origin');
    if (origin && new URL(origin).host !== new URL(request.url).host) return noContent();
    if (!origin && !(request.headers.get('Referer') || '').startsWith(new URL(request.url).origin)) return noContent();
    const ua = request.headers.get('User-Agent') || '';
    if (!ua || BOT_RE.test(ua)) return noContent();
    let body = {};
    try { body = JSON.parse(await request.text()); } catch { body = {}; }
    const path = clip(body.p, 300);
    // '//host' and '/\host' would render as off-site links in visits.html
    if (!path || path[0] !== '/' || path.startsWith('//') || path.includes('\\')) return noContent();
    const vid = typeof body.v === 'string' && VID_RE.test(body.v) ? body.v : null;
    if (body.v && !vid) return noContent();
    const cf = request.cf || {};
    const now = Math.floor(Date.now() / 1000);
    // leave beacon: how long the page was visible, written onto that view's row
    if (body.t === 'leave') {
      const dur = Math.round(Number(body.dur));
      if (vid && Number.isFinite(dur) && dur >= 0 && dur <= 86400) {
        await env.DB.prepare('UPDATE visits SET dur = ? WHERE id = (SELECT id FROM visits WHERE ts >= ? AND vid = ? AND path = ? ORDER BY ts DESC LIMIT 1)')
          .bind(dur, now - 6 * 3600, vid, path).run();
      }
      return noContent();
    }
    const dev = ['phone', 'tablet', 'desktop'].includes(body.d) ? body.d : null;
    const ip = request.headers.get('CF-Connecting-IP') || null;
    if (ip) {
      const recent = await env.DB.prepare('SELECT COUNT(*) AS n FROM visits WHERE ts >= ? AND ip = ?')
        .bind(now - 600, ip).first();
      if (recent && recent.n >= MAX_PER_IP) return noContent();
    }
    await env.DB.prepare(
      'INSERT INTO visits (ts, path, ref, ip, country, city, region, asn, org, tz, ua, vid, owner, lang, dtz, scr, dev, model, postal) ' +
      'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    ).bind(
      now, path, clip(body.r, 300) || null,
      ip,
      cf.country || null, cf.city || null, cf.region || null,
      Number.isFinite(cf.asn) ? cf.asn : null, clip(cf.asOrganization, 120) || null,
      cf.timezone || null, clip(ua, 300), vid, body.o ? 1 : 0,
      clip(body.l, 35) || null, clip(body.z, 64) || null, clip(body.s, 24) || null, dev,
      clip(body.m, 60) || null, clip(cf.postalCode, 16) || null,
    ).run();
    if (Math.random() < 0.005) {
      await env.DB.prepare('DELETE FROM visits WHERE ts < ?').bind(now - RETENTION_S).run();
    }
  } catch (e) {
    // swallow: a lost hit is better than a broken page
  }
  return noContent();
}

export async function onRequest() {
  return new Response(null, { status: 405, headers: { Allow: 'POST', 'Cache-Control': 'no-store' } });
}
