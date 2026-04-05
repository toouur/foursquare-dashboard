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
  'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
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
  // 2. Month view (returns all check‑ins for a given calendar month)
  // --------------------------------------------------------------
  const wantMonth = url.searchParams.get('month');
  if (wantMonth && /^\d{4}-\d{2}$/.test(wantMonth)) {
    const [yr, mo] = wantMonth.split('-').map(Number);
    const tsStart = Math.floor(Date.UTC(yr, mo - 1, 1) / 1000);
    const tsEnd   = Math.floor(Date.UTC(yr, mo,     1) / 1000);
    const dataRes = await env.DB.prepare(
      'SELECT date, venue, city, country, category, venue_id, lat, lng, id ' +
      'FROM checkins WHERE date >= ?1 AND date < ?2 ORDER BY date DESC'
    ).bind(tsStart, tsEnd).all();
    const rows = dataRes.results || [];
    const items = mapRows(rows, {});
    return jsonResp({ items, total: items.length });
  }

  // --------------------------------------------------------------
  // 3. Cursor‑based infinite scroll (default)
  // --------------------------------------------------------------
  const limit = Math.min(50, Math.max(1, parseInt(url.searchParams.get('limit') || '20', 10)));
  const cursor = url.searchParams.get('cursor'); // Unix timestamp integer

  let query = `
    SELECT date, venue, city, country, category, venue_id, lat, lng, id
    FROM checkins
  `;
  const params = [];
  let bindIndex = 1; // <-- Start dynamic index at 1

  if (cursor && !isNaN(parseInt(cursor, 10))) {
    query += ` WHERE date < ?${bindIndex++}`; // Uses ?1, then increments to 2
    params.push(parseInt(cursor, 10));
  }

  // Uses ?1 (if no cursor) or ?2 (if cursor existed)
  query += ` ORDER BY date DESC LIMIT ?${bindIndex}`; 
  params.push(limit + 1); // fetch one extra to detect has_more

  const dataRes = await env.DB.prepare(query).bind(...params).all();
  const rows = dataRes.results || [];

  let has_more = false;
  let items = rows;
  if (rows.length > limit) {
    has_more = true;
    items = rows.slice(0, limit);
  }

  const next_cursor = has_more ? items[items.length - 1].date : null;
  const tzCache = {};
  const mappedItems = mapRows(items, tzCache);

  return jsonResp({
    items: mappedItems,
    has_more,
    next_cursor,
  });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: HEADERS });
}

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: HEADERS });
}