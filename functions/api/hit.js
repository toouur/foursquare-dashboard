// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — POST /api/hit
 *
 * Own page-view counter (Cloudflare Web Analytics has no IPs, cities or unique
 * visitors). Every page sends one navigator.sendBeacon('/api/hit', {p, r, v, o})
 * — the snippet is injected by build.py next to the Web Analytics beacon:
 *   p  path (+ #hash on /venue[.html], where the hash IS the page)
 *   r  document.referrer
 *   v  random visitor id from localStorage (no cookie)
 *   o  1 when this browser belongs to the owner (set by opening visits.html)
 *   k  unique page-view id · l navigator.language · z device timezone
 *   s  screen "WxH@dpr" · d device type · m Android model (high-entropy client hints)
 *   u  utm_* tags of the landing URL, "source=x&medium=y&campaign=z" (empty when none)
 * A {t:'meta'} beacon patches in the model when reported; {t:'leave'} writes
 * visible seconds, scroll depth (sc, %) and Web Vitals (lcp/inp/ttfb ms, cls ×1000)
 * to the exact view row. Neither update creates another view.
 * {t:'ev', e:'search'|'out', x} records a site search query or an outbound click
 * in visit_events (at most MAX_EVENTS per visitor per 10 minutes).
 * Instant alerts: with TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set as Pages secrets,
 * a non-owner view from a country, off-site referrer host or utm tag set not seen
 * in the last 90 days sends one Telegram message (after the response, waitUntil).
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
const MAX_EVENTS = 60;          // search / outbound events per visitor per 10 minutes
const EVENT_KINDS = ['search', 'out'];
const VID_RE = /^[a-z0-9]{8,40}$/;
const VIEW_ID_RE = /^[a-z0-9]{8,40}$/;
const BOT_RE = /bot|crawl|spider|slurp|headless|lighthouse|pagespeed|preview|fetch|monitor|uptime|python|curl|wget|axios|node-fetch|playwright|puppeteer/i;
const noContent = () => new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });

const clip = (s, n) => (typeof s === 'string' ? s.slice(0, n) : null);
const int = (v, lo, hi) => { const n = Math.round(Number(v)); return v !== undefined && v !== null && v !== '' && Number.isFinite(n) && n >= lo && n <= hi ? n : null; };
const hostOf = (u) => { try { return new URL(u).host; } catch { return null; } };
const escHtml = (s) => String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);

// What about this view is new in the last 90 days (each check is one LIMIT 1 read,
// run only when the view carries that kind of signal), for the instant alert.
async function novelty(env, now, row, ownHost) {
  const since = now - RETENTION_S, found = [];
  const seen = async (sql, ...args) => !!(await env.DB.prepare(sql).bind(...args).first());
  if (row.country && !(await seen('SELECT 1 FROM visits WHERE ts >= ? AND owner = 0 AND country = ? LIMIT 1', since, row.country))) {
    found.push('new country ' + row.country);
  }
  const host = row.ref ? hostOf(row.ref) : null;
  if (host && host !== ownHost &&
      !(await seen("SELECT 1 FROM visits WHERE ts >= ? AND owner = 0 AND (ref LIKE ? OR ref LIKE ?) LIMIT 1",
        since, 'https://' + host + '/%', 'http://' + host + '/%'))) {
    found.push('new source ' + host);
  }
  if (row.utm && !(await seen('SELECT 1 FROM visits WHERE ts >= ? AND owner = 0 AND utm = ? LIMIT 1', since, row.utm))) {
    found.push('new utm ' + row.utm);
  }
  return found;
}

async function sendAlert(env, found, row) {
  const text = '<b>🆕 ' + escHtml(found.join(' · ')) + '</b>\n' +
    escHtml([row.city, row.country].filter(Boolean).join(', ')) + ' · ' + escHtml(row.org || '—') + '\n' +
    escHtml(row.path) + (row.ref ? ' · from ' + escHtml(row.ref) : '') +
    (row.dev || row.model ? '\n' + escHtml([row.dev, row.model].filter(Boolean).join(' · ')) : '');
  await fetch('https://api.telegram.org/bot' + env.TELEGRAM_BOT_TOKEN + '/sendMessage', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: 'HTML', disable_web_page_preview: true }),
  });
}

// Coarse device signature from metadata already collected for ordinary analytics.
// It groups similar browser profiles; it is not a guaranteed physical-device ID.
// The model is left out on purpose: it arrives later in the {t:'meta'} beacon and
// only sometimes, so hashing it would split one browser into two fingerprints.
async function deviceFingerprint(ua, body) {
  try {
    const parts = [
      ua.slice(0, 300), clip(body.l, 35) || '', clip(body.z, 64) || '',
      clip(body.s, 24) || '', ['phone', 'tablet', 'desktop'].includes(body.d) ? body.d : ''
    ];
    const bytes = new TextEncoder().encode(parts.join('|'));
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  } catch { return null; }
}

export async function onRequestPost({ request, env, waitUntil }) {
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
    const viewId = typeof body.k === 'string' && VIEW_ID_RE.test(body.k) ? body.k : null;

    // Secondary beacons update the exact page-view row; they must never count as new views.
    if (body.t === 'leave' || body.t === 'meta') {
      if (vid && viewId) {
        const match = ' WHERE id = (SELECT id FROM visits WHERE ts >= ? AND vid = ? AND path = ? AND view_id = ? ORDER BY ts DESC LIMIT 1)';
        if (body.t === 'leave') {
          const dur = int(body.dur, 0, 86400);
          if (dur !== null) {
            const where = [now - 26 * 3600, vid, path, viewId];
            try {
              await env.DB.prepare('UPDATE visits SET dur = ?, scroll = MAX(COALESCE(scroll, 0), COALESCE(?, 0)), ' +
                'lcp = COALESCE(?, lcp), cls = COALESCE(?, cls), inp = COALESCE(?, inp), ttfb = COALESCE(?, ttfb)' + match)
                .bind(dur, int(body.sc, 0, 100), int(body.lcp, 0, 120000), int(body.cls, 0, 100000),
                  int(body.inp, 0, 60000), int(body.ttfb, 0, 120000), ...where).run();
            } catch {
              // scroll / vitals columns not migrated yet: keep the visible time at least
              await env.DB.prepare('UPDATE visits SET dur = ?' + match).bind(dur, ...where).run();
            }
          }
        } else {
          const model = clip(body.m, 60);
          if (model) {
            await env.DB.prepare('UPDATE visits SET model = ?' + match)
              .bind(model, now - 26 * 3600, vid, path, viewId).run();
          }
        }
      }
      return noContent();
    }

    const ip = request.headers.get('CF-Connecting-IP') || null;

    if (body.t === 'ev') {
      const data = clip(body.x, 200);
      if (vid && EVENT_KINDS.includes(body.e) && data) {
        const recent = await env.DB.prepare('SELECT COUNT(*) AS n FROM visit_events WHERE ts >= ? AND vid = ?')
          .bind(now - 600, vid).first();
        if (!recent || recent.n < MAX_EVENTS) {
          await env.DB.prepare('INSERT INTO visit_events (ts, vid, ip, country, path, kind, data, owner) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
            .bind(now, vid, ip, cf.country || null, path, body.e, data, body.o ? 1 : 0).run();
        }
      }
      return noContent();
    }

    const dev = ['phone', 'tablet', 'desktop'].includes(body.d) ? body.d : null;
    if (ip) {
      const recent = await env.DB.prepare('SELECT COUNT(*) AS n FROM visits WHERE ts >= ? AND ip = ?')
        .bind(now - 600, ip).first();
      if (recent && recent.n >= MAX_PER_IP) return noContent();
    }
    const fp = await deviceFingerprint(ua, body);
    const utm = clip(body.u, 300) || null;
    const alertRow = { country: cf.country || null, city: cf.city || null, org: clip(cf.asOrganization, 120) || null,
      path, ref: clip(body.r, 300) || null, utm, dev, model: clip(body.m, 60) || null };
    let found = [];
    if (!body.o && env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
      try { found = await novelty(env, now, alertRow, new URL(request.url).host); } catch { found = []; }
    }
    const values = [
      now, path, clip(body.r, 300) || null,
      ip,
      cf.country || null, cf.city || null, cf.region || null,
      Number.isFinite(cf.asn) ? cf.asn : null, clip(cf.asOrganization, 120) || null,
      cf.timezone || null, clip(ua, 300), vid, body.o ? 1 : 0, viewId,
      clip(body.l, 35) || null, clip(body.z, 64) || null, clip(body.s, 24) || null, dev,
      clip(body.m, 60) || null, clip(cf.postalCode, 16) || null, fp,
    ];
    const cols = 'ts, path, ref, ip, country, city, region, asn, org, tz, ua, vid, owner, view_id, lang, dtz, scr, dev, model, postal, fp';
    const insert = (extra, extraValues) => env.DB.prepare(
      'INSERT INTO visits (' + cols + extra + ') VALUES (' + values.concat(extraValues).map(() => '?').join(', ') + ')'
    ).bind(...values, ...extraValues).run();
    try {
      await insert(', utm', [utm]);
    } catch (e) {
      // the utm column not migrated yet: still count the view, just without its tags
      if (!/utm/i.test(String(e && e.message || e))) throw e;
      await insert('', []);
    }
    if (found.length) {
      const job = sendAlert(env, found, alertRow).catch(() => {});
      if (typeof waitUntil === 'function') waitUntil(job); else await job;
    }
    if (Math.random() < 0.005) {
      await env.DB.prepare('DELETE FROM visits WHERE ts < ?').bind(now - RETENTION_S).run();
      await env.DB.prepare('DELETE FROM visit_events WHERE ts < ?').bind(now - RETENTION_S).run().catch(() => {});
    }
  } catch (e) {
    // swallow: a lost hit is better than a broken page
  }
  return noContent();
}

export async function onRequest() {
  return new Response(null, { status: 405, headers: { Allow: 'POST', 'Cache-Control': 'no-store' } });
}
