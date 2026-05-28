// Copyright 2026 Andrei Patsiomkin
// SPDX-License-Identifier: Apache-2.0

/**
 * Cloudflare Pages Function — /api/feed
 *
 * GET /api/feed?limit=20&cursor=1234567890
 *   → Cursor-based infinite scroll (fast, O(1) reads)
 *   Response: { items: [...], has_more: bool, next_cursor: int|null }
 *
 * GET /api/feed?month=YYYY-MM
 *   → Returns all check‑ins in that calendar month (UTC boundaries)
 *   Response: { items: [...], total: N }
 *
 * GET /api/feed?oldest=1&limit=100
 *   → Returns the N oldest check-ins, newest-first (so caller can reverse for display).
 *   Response: { items: [...], total: N }
 *
 * GET /api/feed?resolve=1234567890
 *   → Returns a cursor that loads items *older than* the given timestamp.
 *     Useful for jumping to a specific date (e.g., oldest check‑in).
 *   Response: { cursor: int|null }
 *
 * The month index (ym_index) and total count are now static JSON (feed_meta.json)
 * generated at build time – this function no longer queries D1 for them.
 */

const HEADERS = {
  'Content-Type': 'application/json',
  // Edge cache 1 h, browser cache 1 min, allow 10 min stale-while-revalidate.
  // Safe to be aggressive because every /api/feed fetch URL carries a `_v=`
  // schema-version param — bump it whenever the response tuple shape changes
  // (e.g. _v=companions) to force a clean cache key.  Newest check-ins are
  // delayed up to 1 h on CDN edge nodes but D1 reads stay near zero.
  'Cache-Control': 'public, max-age=60, s-maxage=3600, stale-while-revalidate=600',
};

// Country → IANA timezone (same as original)
const COUNTRY_TZ = {
  'Belarus':'Europe/Minsk','Moldova':'Europe/Chisinau','Poland':'Europe/Warsaw',
  'Ukraine':'Europe/Kyiv','Italy':'Europe/Rome','Romania':'Europe/Bucharest',
  'Lithuania':'Europe/Vilnius','Germany':'Europe/Berlin','Türkiye':'Europe/Istanbul',
  'Turkey':'Europe/Istanbul','China':'Asia/Shanghai','Spain':'Europe/Madrid',
  'Georgia':'Asia/Tbilisi','France':'Europe/Paris','India':'Asia/Kolkata',
  'Latvia':'Europe/Riga','Portugal':'Europe/Lisbon','Iran':'Asia/Tehran',
  'Egypt':'Africa/Cairo','Japan':'Asia/Tokyo','United Kingdom':'Europe/London',
  'Czechia':'Europe/Prague','Czech Republic':'Europe/Prague','Hungary':'Europe/Budapest',
  'Austria':'Europe/Vienna','Switzerland':'Europe/Zurich','Netherlands':'Europe/Amsterdam',
  'Belgium':'Europe/Brussels','Slovakia':'Europe/Bratislava','Bulgaria':'Europe/Sofia',
  'Greece':'Europe/Athens','Croatia':'Europe/Zagreb','Serbia':'Europe/Belgrade',
  'Estonia':'Europe/Tallinn','Finland':'Europe/Helsinki','Sweden':'Europe/Stockholm',
  'Norway':'Europe/Oslo','Denmark':'Europe/Copenhagen','Kazakhstan':'Asia/Almaty',
  'Uzbekistan':'Asia/Tashkent','Azerbaijan':'Asia/Baku','Armenia':'Asia/Yerevan',
  'Israel':'Asia/Jerusalem','Jordan':'Asia/Amman','Thailand':'Asia/Bangkok',
  'Vietnam':'Asia/Ho_Chi_Minh','Indonesia':'Asia/Jakarta','South Korea':'Asia/Seoul',
  'Taiwan':'Asia/Taipei','Singapore':'Asia/Singapore','Malaysia':'Asia/Kuala_Lumpur',
  'Pakistan':'Asia/Karachi','Nepal':'Asia/Kathmandu','Mongolia':'Asia/Ulaanbaatar',
  'Morocco':'Africa/Casablanca','Tunisia':'Africa/Tunis','South Africa':'Africa/Johannesburg',
  'New Zealand':'Pacific/Auckland','Holy See (Vatican City State)':'Europe/Rome',
  'San Marino':'Europe/Rome','Monaco':'Europe/Monaco','Malta':'Europe/Malta',
  'Cyprus':'Asia/Nicosia','Iceland':'Atlantic/Reykjavik','Ireland':'Europe/Dublin',
  'Slovenia':'Europe/Ljubljana','North Macedonia':'Europe/Skopje','Albania':'Europe/Tirane',
  'Montenegro':'Europe/Podgorica','Bosnia and Herzegovina':'Europe/Sarajevo',
  'Kosovo':'Europe/Belgrade','Tajikistan':'Asia/Dushanbe','Kyrgyzstan':'Asia/Bishkek',
  'Turkmenistan':'Asia/Ashgabat','Qatar':'Asia/Qatar','UAE':'Asia/Dubai',
  'United Arab Emirates':'Asia/Dubai','Saudi Arabia':'Asia/Riyadh','Iraq':'Asia/Baghdad',
  'Lebanon':'Asia/Beirut','Hong Kong':'Asia/Hong_Kong','Macao':'Asia/Macau','Macau':'Asia/Macau',
};

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function getTz(country, lng) {
  if (COUNTRY_TZ[country]) return COUNTRY_TZ[country];
  if (country === 'Russia') {
    if (lng == null) return 'Europe/Moscow';
    if (lng < 60)  return 'Europe/Moscow';
    if (lng < 73)  return 'Asia/Yekaterinburg';
    if (lng < 84)  return 'Asia/Omsk';
    if (lng < 98)  return 'Asia/Krasnoyarsk';
    if (lng < 115) return 'Asia/Irkutsk';
    if (lng < 130) return 'Asia/Yakutsk';
    if (lng < 142) return 'Asia/Vladivostok';
    return 'Asia/Magadan';
  }
  if (country === 'Brazil') {
    if (lng == null) return 'America/Sao_Paulo';
    if (lng > -40) return 'America/Fortaleza';
    if (lng > -48) return 'America/Sao_Paulo';
    return 'America/Manaus';
  }
  if (country === 'United States') {
    if (lng == null) return 'America/New_York';
    if (lng > -75)  return 'America/New_York';
    if (lng > -90)  return 'America/Chicago';
    if (lng > -110) return 'America/Denver';
    return 'America/Los_Angeles';
  }
  if (country === 'Australia') {
    if (lng == null) return 'Australia/Sydney';
    if (lng < 129)  return 'Australia/Perth';
    if (lng < 138)  return 'Australia/Darwin';
    if (lng < 142)  return 'Australia/Adelaide';
    return 'Australia/Sydney';
  }
  if (lng != null) {
    const off = Math.round(lng / 15);
    const tz = `Etc/GMT${off <= 0 ? '+' : ''}${-off}`;
    return tz;
  }
  return 'UTC';
}

function formatLocal(ts, tz) {
  try {
    const d = new Date(ts * 1000);
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz,
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
    const parts = fmt.formatToParts(d);
    const p = {};
    for (const { type, value } of parts) p[type] = value;
    const dateStr = `${p.day} ${p.month} ${p.year}`;
    const timeStr = `${p.hour}:${p.minute}`;
    return [dateStr, timeStr];
  } catch {
    const d = new Date(ts * 1000);
    return [
      `${String(d.getUTCDate()).padStart(2,'0')} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
      `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`,
    ];
  }
}

// De-duped union of with_name, created_by_name, overlaps_name — same logic
// as metrics.collect_companions on the Python side.
function collectCompanions(r) {
  const seen = new Set();
  const out = [];
  const pushList = (raw, allowDashSentinel) => {
    if (!raw) return;
    for (const part of String(raw).replace(/ ,/g, ',').split(',')) {
      const n = part.trim();
      if (!n) continue;
      if (!allowDashSentinel && n === '-') continue;
      const key = n.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(n);
    }
  };
  pushList(r.with_name, false);
  const cb = (r.created_by_name || '').trim();
  if (cb && !seen.has(cb.toLowerCase())) {
    seen.add(cb.toLowerCase());
    out.push(cb);
  }
  pushList(r.overlaps_name, false);
  return out;
}

function mapRows(rows, tzCache) {
  return rows.map(r => {
    const lng = r.lng != null ? r.lng : null;
    const key = `${r.country}|${lng != null ? Math.round(lng) : ''}`;
    if (!(key in tzCache)) tzCache[key] = getTz(r.country, lng);
    const [dateStr, timeStr] = formatLocal(r.date, tzCache[key]);
    return [
      r.date,
      dateStr,
      timeStr,
      r.venue     || '',
      r.city      || '',
      r.country   || '',
      r.category  || '',
      r.venue_id  || '',
      r.lat  != null ? Math.round(r.lat  * 10000) / 10000 : null,
      r.lng  != null ? Math.round(r.lng  * 10000) / 10000 : null,
      r.id        || '',
      collectCompanions(r),
    ];
  });
}

export async function onRequestGet({ request, env }) {
  if (!env.DB) {
    return jsonResp({ error: 'DB binding not configured' }, 503);
  }

  const url = new URL(request.url);

  // --------------------------------------------------------------
  // 1. Helper: resolve timestamp to cursor (for jumping to a date)
  // --------------------------------------------------------------
  const resolveTs = url.searchParams.get('resolve');
  if (resolveTs !== null) {
    const ts = parseInt(resolveTs, 10);
    if (isNaN(ts)) {
      return jsonResp({ error: 'Invalid timestamp' }, 400);
    }
    // Find the first check‑in with date <= ts, then return that date as cursor.
    // This ensures loading with cursor=result will show items older than that date.
    const row = await env.DB.prepare(
      'SELECT date FROM checkins WHERE date <= ?1 ORDER BY date DESC LIMIT 1'
    ).bind(ts).first();
    const cursor = row?.date ?? null;
    return jsonResp({ cursor });
  }

  // --------------------------------------------------------------
  // 2. Full-text search across venue/city/country/category
  // GET /api/feed?q=term  → { items: [...], total: N }
  // --------------------------------------------------------------
  const wantSearch = url.searchParams.get('q');
  if (wantSearch !== null) {
    const term = wantSearch.trim();
    if (!term) return jsonResp({ items: [], total: 0 });
    const like = `%${term}%`;
    const dataRes = await env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name ' +
      'FROM checkins WHERE venue LIKE ?1 OR city LIKE ?1 OR country LIKE ?1 OR category LIKE ?1 ' +
      'ORDER BY date DESC LIMIT 500'
    ).bind(like).all();
    const rows = dataRes.results || [];
    const items = mapRows(rows, {});
    return jsonResp({ items, total: items.length });
  }

  // --------------------------------------------------------------
  // 3. Month view (returns all check‑ins for a given calendar month)
  // --------------------------------------------------------------
  const wantMonth = url.searchParams.get('month');
  if (wantMonth && /^\d{4}-\d{2}$/.test(wantMonth)) {
    const [yr, mo] = wantMonth.split('-').map(Number);
    // Pad ±1 day (86 400 s) so check-ins near a UTC month boundary that fall in a
    // different local month are still returned.  The client filters to local month.
    const PAD = 86400;
    const tsStart = Math.floor(Date.UTC(yr, mo - 1, 1) / 1000) - PAD;
    const tsEnd   = Math.floor(Date.UTC(yr, mo,     1) / 1000) + PAD;
    const dataRes = await env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name ' +
      'FROM checkins WHERE date >= ?1 AND date < ?2 ORDER BY date DESC'
    ).bind(tsStart, tsEnd).all();
    const rows = dataRes.results || [];
    const items = mapRows(rows, {});
    return jsonResp({ items, total: items.length });
  }

  // --------------------------------------------------------------
  // 3. Oldest N check-ins (for "Oldest" button fast-path)
  // --------------------------------------------------------------
  const wantOldest = url.searchParams.get('oldest');
  if (wantOldest !== null) {
    const lim = Math.min(200, Math.max(1, parseInt(url.searchParams.get('limit') || '100', 10)));
    const dataRes = await env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name ' +
      'FROM checkins ORDER BY date ASC LIMIT ?1'
    ).bind(lim).all();
    const rows = dataRes.results || [];
    const items = mapRows(rows.reverse(), {}); // newest-first so caller can reverse if needed
    return jsonResp({ items, total: items.length });
  }

  // --------------------------------------------------------------
  // 4. Reverse cursor — items NEWER than a timestamp (gap fill from bottom up)
  // GET /api/feed?after=TS&limit=N
  //   → Items with date > TS, ASC order then reversed → newest-first
  //   Response: { items: [...], has_more: bool }
  // --------------------------------------------------------------
  const wantAfter = url.searchParams.get('after');
  if (wantAfter !== null) {
    const ts = parseInt(wantAfter, 10);
    if (isNaN(ts)) return jsonResp({ error: 'Invalid timestamp' }, 400);
    const afterId = url.searchParams.get('after_id') || null;
    const lim = Math.min(50, Math.max(1, parseInt(url.searchParams.get('limit') || '50', 10)));
    // Compound cursor: items newer than (ts, afterId) to handle duplicate timestamps
    const whereClause = afterId
      ? 'WHERE (date > ?1 OR (date = ?1 AND id > ?2))'
      : 'WHERE date > ?1';
    const bindArgs = afterId ? [ts, afterId, lim + 1] : [ts, lim + 1];
    const limitIdx = afterId ? '?3' : '?2';
    const dataRes = await env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name ' +
      `FROM checkins ${whereClause} ORDER BY date ASC, id ASC LIMIT ${limitIdx}`
    ).bind(...bindArgs).all();
    const rows = dataRes.results || [];
    const has_more = rows.length > lim;
    const trimmed = has_more ? rows.slice(0, lim) : rows;
    const items = mapRows(trimmed.reverse(), {});
    return jsonResp({ items, has_more });
  }

  // --------------------------------------------------------------
  // 5. Country or city exact-match, cursor-paginated
  // GET /api/feed?country=X[&cursor=TS&cursor_id=ID&limit=N]
  // GET /api/feed?city=X[&cursor=TS&cursor_id=ID&limit=N]
  //   → { items: [...], has_more: bool, next_cursor: int|null, next_cursor_id: str|null, total: N }
  // --------------------------------------------------------------
  const filterCountry = url.searchParams.get('country');
  const filterCity    = url.searchParams.get('city');
  if (filterCountry !== null || filterCity !== null) {
    const flimit = Math.min(50, Math.max(1, parseInt(url.searchParams.get('limit') || '50', 10)));
    const fcursor   = url.searchParams.get('cursor');
    const fcursorId = url.searchParams.get('cursor_id');
    const fcursorTs = fcursor ? parseInt(fcursor, 10) : NaN;

    const filterCol = filterCountry !== null ? 'country' : 'city';
    const filterVal = filterCountry !== null ? filterCountry : filterCity;

    let fwhere = `${filterCol} = ?1`;
    const fparams = [filterVal];
    let fidx = 2;

    if (!isNaN(fcursorTs)) {
      if (fcursorId) {
        fwhere += ` AND (date < ?${fidx} OR (date = ?${fidx} AND id < ?${fidx+1}))`;
        fparams.push(fcursorTs, fcursorId);
        fidx += 2;
      } else {
        fwhere += ` AND date < ?${fidx}`;
        fparams.push(fcursorTs);
        fidx++;
      }
    }

    const dataRes = await env.DB.prepare(
      `SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name ` +
      `FROM checkins WHERE ${fwhere} ORDER BY date DESC, id DESC LIMIT ?${fidx}`
    ).bind(...fparams, flimit + 1).all();

    const frows = dataRes.results || [];
    const fhas_more = frows.length > flimit;
    const ftrimmed = fhas_more ? frows.slice(0, flimit) : frows;
    const flast = ftrimmed[ftrimmed.length - 1];
    const fitems = mapRows(ftrimmed, {});
    return jsonResp({
      items: fitems,
      has_more: fhas_more,
      next_cursor:    fhas_more ? flast.date : null,
      next_cursor_id: fhas_more ? flast.id   : null,
    });
  }

  // --------------------------------------------------------------
  // 6. Cursor‑based infinite scroll (default)
  // --------------------------------------------------------------
  const limit = Math.min(50, Math.max(1, parseInt(url.searchParams.get('limit') || '20', 10)));
  const cursor   = url.searchParams.get('cursor');    // Unix timestamp integer
  const cursorId = url.searchParams.get('cursor_id'); // secondary id for compound cursor

  const params = [];
  let bindIndex = 1;
  let whereClause = '';

  const cursorTs = cursor ? parseInt(cursor, 10) : NaN;
  if (!isNaN(cursorTs)) {
    if (cursorId) {
      // Compound cursor: items strictly older than (cursorTs, cursorId)
      whereClause = ` WHERE (date < ?${bindIndex} OR (date = ?${bindIndex} AND id < ?${bindIndex + 1}))`;
      params.push(cursorTs, cursorId);
      bindIndex += 2;
    } else {
      whereClause = ` WHERE date < ?${bindIndex++}`;
      params.push(cursorTs);
    }
  }

  const query = `
    SELECT date, venue, city, country, category, venue_id, lat, lng, id, with_name, created_by_name, overlaps_name
    FROM checkins${whereClause}
    ORDER BY date DESC, id DESC LIMIT ?${bindIndex}`;
  params.push(limit + 1); // fetch one extra to detect has_more

  const dataRes = await env.DB.prepare(query).bind(...params).all();
  const rows = dataRes.results || [];

  let has_more = false;
  let items = rows;
  if (rows.length > limit) {
    has_more = true;
    items = rows.slice(0, limit);
  }

  const lastItem = items[items.length - 1];
  const next_cursor    = has_more ? lastItem.date : null;
  const next_cursor_id = has_more ? lastItem.id   : null;
  const tzCache = {};
  const mappedItems = mapRows(items, tzCache);

  return jsonResp({
    items: mappedItems,
    has_more,
    next_cursor,
    next_cursor_id,
  });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: HEADERS });
}